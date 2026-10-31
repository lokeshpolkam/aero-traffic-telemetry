"""
Edge-AI Multi-Target Telemetry & Trajectory Tracking Engine
Powered by YOLOv8 and ByteTrack (Kalman Filter + Hungarian Algorithm)
Computes real-time object trajectories, velocity estimation, and virtual line counting.
"""

import os
import cv2
import numpy as np
from collections import defaultdict, deque
import torch

# PyTorch 2.6+ compatibility: allow DetectionModel and patch weights_only default
try:
    from ultralytics.nn.tasks import DetectionModel
    if hasattr(torch.serialization, "add_safe_globals"):
        torch.serialization.add_safe_globals([DetectionModel])
except Exception:
    pass

_orig_torch_load = torch.load
def _safe_torch_load(*args, **kwargs):
    if "weights_only" not in kwargs:
        kwargs["weights_only"] = False
    return _orig_torch_load(*args, **kwargs)
torch.load = _safe_torch_load

from ultralytics import YOLO

# Standard COCO class mappings for surveillance
CLASS_NAMES = {
    0: "Person",
    1: "Bicycle",
    2: "Car",
    3: "Motorcycle",
    5: "Bus",
    7: "Truck"
}

# High-contrast cyber-surveillance color palette (BGR format)
CLASS_COLORS = {
    "Person": (50, 255, 120),       # Lime Green
    "Car": (255, 180, 0),           # Bright Cyan/Sky Blue
    "Truck": (0, 215, 255),         # Vibrant Yellow-Gold
    "Bus": (255, 50, 220),          # Magenta
    "Motorcycle": (0, 140, 255),    # Deep Orange
    "Bicycle": (220, 20, 60)        # Crimson
}

class EdgeVisionTracker:
    def __init__(self, model_path="yolov8n.pt", max_trail_len=30):
        # Resolve local YOLOv8 weights (zero cloud download required)
        if not os.path.isabs(model_path):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            candidate = os.path.join(base_dir, model_path)
            if os.path.exists(candidate):
                model_path = candidate
        self.model = YOLO(model_path)
        self.max_trail_len = max_trail_len
        
        # Spatial trajectory tracking memory: {track_id: deque([(x, y, timestamp), ...])}
        self.track_history = defaultdict(lambda: deque(maxlen=self.max_trail_len))
        
        # Virtual counting line memory
        self.counted_inbound = set()
        self.counted_outbound = set()
        
        # Speed smoothing cache: {track_id: smoothed_speed}
        self.speed_cache = {}
        
    def reset_state(self):
        """Resets trajectory trails and counting line tallies."""
        self.track_history.clear()
        self.counted_inbound.clear()
        self.counted_outbound.clear()
        self.speed_cache.clear()

    def process_frame(
        self,
        frame,
        conf_thresh=0.35,
        allowed_classes=None,
        show_trails=True,
        show_speed=True,
        show_line=True,
        show_hud=True,
        line_y_ratio=0.60
    ):
        """
        Executes YOLOv8 object detection and ByteTrack temporal association on a single frame.
        
        Returns:
            annotated_frame (np.ndarray): Visual output with bounding boxes, IDs, and HUD overlays.
            telemetry_data (dict): Quantitative real-time metrics (flow, active targets, speeds).
        """
        h_frame, w_frame = frame.shape[:2]
        line_y = int(h_frame * line_y_ratio)
        
        # Allowed class indices in COCO (0=person, 2=car, 3=motorcycle, 5=bus, 7=truck)
        target_indices = [0, 1, 2, 3, 5, 7]
        
        # Run YOLOv8 + ByteTrack on the frame
        results = self.model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            conf=conf_thresh,
            classes=target_indices,
            verbose=False
        )[0]
        
        annotated = frame.copy()
        active_targets = []
        current_frame_ids = set()
        
        # Draw Virtual Counting Line if enabled
        if show_line:
            # Neon Green/Cyan counting line across the road
            cv2.line(annotated, (0, line_y), (w_frame, line_y), (0, 255, 200), 2, cv2.LINE_AA)
            cv2.putText(
                annotated,
                f"VIRTUAL TELEMETRY LINE [Y={line_y}]",
                (15, line_y - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 255, 200),
                1,
                cv2.LINE_AA
            )
            
        if results.boxes.id is not None:
            boxes = results.boxes.xyxy.cpu().numpy()
            track_ids = results.boxes.id.int().cpu().numpy()
            class_ids = results.boxes.cls.int().cpu().numpy()
            confidences = results.boxes.conf.cpu().numpy()
            
            for box, track_id, cls_id, conf in zip(boxes, track_ids, class_ids, confidences):
                c_name = CLASS_NAMES.get(cls_id, "Target")
                if allowed_classes and c_name not in allowed_classes:
                    continue
                    
                x1, y1, x2, y2 = map(int, box)
                cx = int((x1 + x2) / 2)
                cy = int((y1 + y2) / 2)
                
                current_frame_ids.add(track_id)
                color = CLASS_COLORS.get(c_name, (0, 255, 255))
                
                # Update spatial trajectory history
                prev_points = list(self.track_history[track_id])
                self.track_history[track_id].append((cx, cy))
                
                # Velocity Estimation via spatial displacement
                speed_kmh = 0.0
                if len(prev_points) >= 3:
                    # Calculate displacement over last 5 frames
                    lookback = min(5, len(prev_points))
                    px_prev, py_prev = prev_points[-lookback]
                    dist_px = np.sqrt((cx - px_prev)**2 + (cy - py_prev)**2)
                    
                    # Perspective scale calibration (~0.065 meters per pixel in typical traffic FOV)
                    # Speed (km/h) = (meters / sec) * 3.6, assuming ~25 FPS
                    raw_speed = (dist_px * 0.065 * 25.0 / lookback) * 3.6
                    
                    # Exponential smoothing to prevent velocity jitter
                    if track_id in self.speed_cache:
                        speed_kmh = 0.7 * self.speed_cache[track_id] + 0.3 * raw_speed
                    else:
                        speed_kmh = raw_speed
                    self.speed_cache[track_id] = speed_kmh
                elif track_id in self.speed_cache:
                    speed_kmh = self.speed_cache[track_id]
                    
                # Virtual Counting Line Collision Detection
                if len(prev_points) >= 2:
                    y_old = prev_points[-2][1]
                    if y_old < line_y and cy >= line_y:
                        if track_id not in self.counted_inbound:
                            self.counted_inbound.add(track_id)
                    elif y_old > line_y and cy <= line_y:
                        if track_id not in self.counted_outbound:
                            self.counted_outbound.add(track_id)
                            
                # Draw Glowing Trajectory Trail
                if show_trails and len(self.track_history[track_id]) > 1:
                    points = np.array(self.track_history[track_id], dtype=np.int32)
                    for idx in range(1, len(points)):
                        thickness = int(np.sqrt(idx / len(points)) * 3) + 1
                        cv2.line(annotated, tuple(points[idx-1]), tuple(points[idx]), color, thickness, cv2.LINE_AA)
                        
                # Draw Bounding Box with rounded corners aesthetic
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                
                # Draw Bounding Badge
                speed_str = f" | {speed_kmh:.1f} km/h" if show_speed and speed_kmh > 0 else ""
                label = f"#{track_id} {c_name}{speed_str}"
                
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
                cv2.rectangle(annotated, (x1, max(0, y1 - 20)), (x1 + tw + 6, max(20, y1)), color, -1)
                cv2.putText(annotated, label, (x1 + 3, max(14, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 0, 0), 1, cv2.LINE_AA)
                
                active_targets.append({
                    "id": int(track_id),
                    "class": c_name,
                    "confidence": round(float(conf) * 100, 1),
                    "speed_kmh": round(float(speed_kmh), 1),
                    "position": (cx, cy),
                    "trail_len": len(self.track_history[track_id])
                })
                
        # Clean up memory for disappeared tracks (>60 frames without detection)
        active_ids = set(self.track_history.keys())
        for tid in list(active_ids):
            if tid not in current_frame_ids and len(self.track_history[tid]) > 0:
                self.track_history[tid].popleft()
                if len(self.track_history[tid]) == 0:
                    del self.track_history[tid]
                    self.speed_cache.pop(tid, None)
                    
        # Compute Congestion Level
        target_count = len(active_targets)
        if target_count <= 2:
            congestion_str = "FLUID FLOW"
            congestion_color = (50, 255, 120)
        elif target_count <= 6:
            congestion_str = "MODERATE TRAFFIC"
            congestion_color = (0, 215, 255)
        else:
            congestion_str = "HEAVY CONGESTION"
            congestion_color = (0, 50, 255)
            
        valid_speeds = [t["speed_kmh"] for t in active_targets if t["speed_kmh"] > 0]
        avg_speed = round(float(np.mean(valid_speeds)), 1) if len(valid_speeds) > 0 else 0.0
        
        # Render Cyberpunk Top HUD Banner if enabled
        if show_hud:
            hud_h = 42
            overlay = annotated.copy()
            cv2.rectangle(overlay, (0, 0), (w_frame, hud_h), (20, 24, 33), -1)
            cv2.addWeighted(overlay, 0.85, annotated, 0.15, 0, annotated)
            cv2.line(annotated, (0, hud_h), (w_frame, hud_h), (50, 60, 80), 1)
            
            hud_text = (
                f"ACTIVE TARGETS: {target_count:02d}  |  "
                f"INBOUND: {len(self.counted_inbound):03d}  |  "
                f"OUTBOUND: {len(self.counted_outbound):03d}  |  "
                f"AVG SPEED: {avg_speed:.1f} KM/H  |  "
                f"STATUS: {congestion_str}"
            )
            cv2.putText(annotated, hud_text, (15, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)
            # Dot indicator for status
            cv2.circle(annotated, (w_frame - 25, 22), 6, congestion_color, -1)
            
        telemetry_data = {
            "active_count": target_count,
            "inbound_count": len(self.counted_inbound),
            "outbound_count": len(self.counted_outbound),
            "total_flow": len(self.counted_inbound) + len(self.counted_outbound),
            "avg_speed_kmh": avg_speed,
            "congestion_status": congestion_str,
            "active_targets": active_targets
        }
        
        return annotated, telemetry_data
