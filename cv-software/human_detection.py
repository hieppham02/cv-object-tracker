"""Person-detection logic used by the Tkinter interface."""

from pathlib import Path
from time import monotonic

import cv2
import numpy as np
import serial
import torch
from ultralytics import YOLO


MODEL_PATH = Path(__file__).resolve().parent.parent / "yolo11n.pt"
SERIAL_PORT = "COM6"
SERIAL_BAUD_RATE = 9600
PERSON_CLASS_ID = 0
CONFIDENCE_THRESHOLD = 0.5
IMAGE_SIZE = 416
CENTER_ANGLE = 90

# fine-tune configs
PROPORTIONAL_GAIN = 0.025
DEAD_ZONE_PIXELS = 30
ERROR_ALPHA = 0.75
MAX_STEP = 4
CONTROL_INTERVAL = 0.1

SERVO_DIRECTION = 1  # Set to -1 if positive error turns the camera away.
MIN_ANGLE = 0
MAX_ANGLE = 180
MIN_SEND_CHANGE = 1
TARGET_MISSING_FRAMES = 5


class HumanDetector:
    """Detect people and draw tracking information on an OpenCV frame."""

    def __init__(self) -> None:
        self.device = 0 if torch.cuda.is_available() else "cpu"
        self.model = YOLO(MODEL_PATH)
        self.serial_connection = self._open_serial_connection()
        self.last_sent_angle = None
        self.servo_angle = float(CENTER_ANGLE)
        self.filtered_error = 0.0
        self.last_control_time = None
        self.target_center = None
        self.missing_frames = 0
        self.proportional_gain = PROPORTIONAL_GAIN
        self.dead_zone_pixels = DEAD_ZONE_PIXELS
        self.error_alpha = ERROR_ALPHA
        self.max_step = MAX_STEP
        self._warm_up()

    def update_control_parameters(
        self,
        proportional_gain: float,
        dead_zone_pixels: int,
        error_alpha: float,
        max_step: int,
    ) -> None:
        """Apply live controller values from the tuning controls."""
        self.proportional_gain = proportional_gain
        self.dead_zone_pixels = dead_zone_pixels
        self.error_alpha = error_alpha
        self.max_step = max_step

    @staticmethod
    def _open_serial_connection():
        """Open COM6 when available; detection can still run without it."""
        try:
            connection = serial.Serial(SERIAL_PORT, SERIAL_BAUD_RATE, timeout=1)
            print(f"Serial connected: {SERIAL_PORT} ({SERIAL_BAUD_RATE} baud)")
            return connection
        except serial.SerialException as error:
            print(f"Cannot open {SERIAL_PORT}: {error}")
            return None

    def _warm_up(self) -> None:
        """Initialize the inference device before processing real camera frames."""
        sample_frame = np.zeros(
            (IMAGE_SIZE, IMAGE_SIZE, 3),
            dtype=np.uint8,
        )
        self.model(
            sample_frame,
            classes=[PERSON_CLASS_ID],
            imgsz=IMAGE_SIZE,
            device=self.device,
            verbose=False,
        )

    def process_frame(self, frame):
        """Draw detections and follow the previously selected person."""
        frame_height, frame_width = frame.shape[:2]
        screen_center = (frame_width // 2, frame_height // 2)
        cv2.circle(frame, screen_center, 6, (255, 0, 0), -1)

        results = self.model(
            frame,
            classes=[PERSON_CLASS_ID],
            conf=CONFIDENCE_THRESHOLD,
            imgsz=IMAGE_SIZE,
            device=self.device,
            verbose=False,
        )

        targets = []
        for result in results:
            for box in result.boxes:
                target = self._read_target(box, screen_center)
                self._draw_target(frame, target, screen_center)

                targets.append(target)

        best_target = self._select_target(targets, frame_width)
        if best_target is not None:
            servo_angle = self._calculate_servo_angle(best_target["error_x"])
            self._send_angle(servo_angle)
            self._draw_tracking_text(frame, best_target, servo_angle)
        else:
            self.filtered_error = 0.0
            self.last_control_time = None

        return frame

    def _select_target(self, targets: list, frame_width: int):
        """Prefer the nearby previous target over changing confidence scores."""
        if self.target_center is None:
            selected = max(targets, key=lambda target: target["confidence"], default=None)
        else:
            def distance(target):
                return sum((a - b) ** 2 for a, b in zip(target["center"], self.target_center))

            selected = min(targets, key=distance, default=None)
            if selected is not None and distance(selected) > (frame_width * 0.25) ** 2:
                selected = None

        if selected is None:
            self.missing_frames += 1
            if self.missing_frames >= TARGET_MISSING_FRAMES:
                self.target_center = None
                # self._send_angle(CENTER_ANGLE)
            return None

        self.target_center = selected["center"]
        self.missing_frames = 0
        return selected

    def _send_angle(self, angle: int) -> None:
        """Send a changed servo angle as a newline-terminated serial message."""
        rounded_angle = int(round(angle))
        if self.serial_connection is None:
            return
        if self.last_sent_angle is not None and abs(rounded_angle - self.last_sent_angle) < MIN_SEND_CHANGE:
            return

        try:
            self.serial_connection.write(f"{rounded_angle}\n".encode("ascii"))
            print(f"Angle: {rounded_angle}")
            self.last_sent_angle = rounded_angle
        except serial.SerialException as error:
            print(f"Serial send failed: {error}")
            self.serial_connection = None

    def close(self) -> None:
        """Close the serial connection."""
        if self.serial_connection is not None and self.serial_connection.is_open:
            self.serial_connection.close()

    @staticmethod
    def _read_target(box, screen_center: tuple[int, int]) -> dict:
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        person_center = ((x1 + x2) // 2, (y1 + y2) // 2)

        return {
            "box": (x1, y1, x2, y2),
            "confidence": float(box.conf[0]),
            "center": person_center,
            "error_x": person_center[0] - screen_center[0],
        }

    @staticmethod
    def _draw_target(frame, target: dict, screen_center: tuple[int, int]) -> None:
        x1, y1, x2, y2 = target["box"]
        person_center = target["center"]

        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.circle(frame, person_center, 6, (0, 0, 255), -1)
        cv2.line(frame, screen_center, person_center, (0, 255, 255), 2)

    def _calculate_servo_angle(self, error_x: int) -> int:
        """Hold the current angle at center; otherwise apply a small correction."""
        now = monotonic()
        # Stop immediately in the dead zone, including residual EMA motion.
        if abs(error_x) <= self.dead_zone_pixels:
            self.filtered_error = 0.0
            self.last_control_time = now
            return int(round(self.servo_angle))

        # Limit control frequency without sleeping or blocking video updates.
        if self.last_control_time is not None and now - self.last_control_time < CONTROL_INTERVAL:
            return int(round(self.servo_angle))
        self.last_control_time = now
        self.filtered_error = self.error_alpha * error_x + (1 - self.error_alpha) * self.filtered_error
        if abs(self.filtered_error) > self.dead_zone_pixels:
            step = SERVO_DIRECTION * self.proportional_gain * self.filtered_error
            step = max(-self.max_step, min(self.max_step, step))
            self.servo_angle = max(MIN_ANGLE, min(MAX_ANGLE, self.servo_angle + step))
        return int(round(self.servo_angle))

    @staticmethod
    def _draw_tracking_text(frame, target: dict, servo_angle: int) -> None:
        text = (
            f"Person {target['confidence']:.2f} | "
            f"errorX={target['error_x']} | angle={servo_angle}"
        )
        cv2.putText(
            frame,
            text,
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2,
        )
