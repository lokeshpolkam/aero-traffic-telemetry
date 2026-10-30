"""
AERO-TRAFFIC: Autonomous Edge-AI Multi-Target Telemetry & Trajectory Tracking Platform
Powered by YOLOv8 and ByteTrack (Kalman Filter + Hungarian Algorithm)
Streamlit Smart City Operations Dashboard for 6th Semester Capstone Project
"""

import os
import time
import cv2
import numpy as np
import pandas as pd
import streamlit as st
import torch

# PyTorch 2.6+ compatibility
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

from tracker_engine import EdgeVisionTracker, CLASS_NAMES

# Page Configuration
st.set_page_config(
    page_title="AERO-TRAFFIC | Autonomous Edge-AI Multi-Target Telemetry",
    page_icon="🚦",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Cyber-Surveillance Dark Theme Styling
st.markdown("""
<style>
    .main-title {
        font-size: 26px;
        font-weight: 800;
        color: #00e5ff;
        margin-bottom: 2px;
        letter-spacing: -0.5px;
    }
    .sub-title {
        font-size: 13.5px;
        color: #80deea;
        margin-bottom: 18px;
    }
    .metric-card {
        background-color: #111827;
        border: 1px solid #1f2937;
        border-radius: 8px;
        padding: 14px 18px;
        text-align: center;
    }
    .metric-val {
        font-size: 26px;
        font-weight: 800;
        color: #00e5ff;
    }
    .metric-lbl {
        font-size: 12px;
        color: #9ca3af;
        text-transform: uppercase;
        font-weight: 600;
        margin-top: 2px;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def load_tracker():
    """Caches YOLOv8 tracker in RAM for instant inference."""
    return EdgeVisionTracker("yolov8n.pt")

tracker = load_tracker()

# ----------------- SIDEBAR CONTROLS -----------------
st.sidebar.image("https://img.icons8.com/fluency/96/traffic-light.png", width=64)
st.sidebar.title("Surveillance Control")

st.sidebar.markdown("### 1. Feed Configuration")
source_mode = st.sidebar.selectbox(
    "Active Sensor Feed:",
    [
        "Camera-01: Interstate Route 66 (Highway Feed)",
        "Camera-02: Downtown Broadway (Urban Mixed Traffic)",
        "Camera-03: Central Plaza (Pedestrian Foot Traffic)",
        "Upload Custom Video (MP4 / AVI)"
    ]
)

video_path = None
samples_dir = "samples"

if "Camera-01" in source_mode:
    video_path = os.path.join(samples_dir, "highway_traffic_camera_01.mp4")
elif "Camera-02" in source_mode:
    video_path = os.path.join(samples_dir, "urban_intersection_camera_02.mp4")
elif "Camera-03" in source_mode:
    video_path = os.path.join(samples_dir, "pedestrian_plaza_camera_03.mp4")
else:
    uploaded = st.sidebar.file_uploader("Upload Surveillance Footage:", type=["mp4", "avi", "mov"])
    if uploaded is not None:
        temp_path = "temp_custom_upload.mp4"
        with open(temp_path, "wb") as f:
            f.write(uploaded.read())
        video_path = temp_path

st.sidebar.markdown("---")
st.sidebar.markdown("### 2. Edge Detection Parameters")
conf_val = st.sidebar.slider("Detector Confidence Cutoff:", 0.20, 0.85, 0.35, 0.05)
selected_classes = st.sidebar.multiselect(
    "Target Filter:",
    ["Car", "Truck", "Bus", "Motorcycle", "Bicycle", "Person"],
    default=["Car", "Truck", "Bus", "Motorcycle", "Person"]
)
line_y_ratio = st.sidebar.slider("Virtual Line Position (Y-Axis %):", 0.30, 0.85, 0.60, 0.05)

st.sidebar.markdown("---")
st.sidebar.markdown("### 3. Visual Overlays")
show_trails = st.sidebar.checkbox("Neon Trajectory Trails", value=True)
show_speed = st.sidebar.checkbox("Velocity Estimation Badges", value=True)
show_line = st.sidebar.checkbox("Virtual Telemetry Counting Line", value=True)
show_hud = st.sidebar.checkbox("Cyberpunk HUD Banner", value=True)

st.sidebar.markdown("---")
st.sidebar.markdown("### 4. Playback Engine")
max_frames = st.sidebar.slider("Process Frame Budget:", 50, 350, 150, 25)
frame_skip = st.sidebar.select_slider("Temporal Downsampling (Speedup):", options=[1, 2, 3], value=1)
run_pipeline = st.sidebar.button("▶ Start Video Tracking Pipeline", type="primary")

# ----------------- MAIN DASHBOARD -----------------
st.markdown("<div class='main-title'>AERO-TRAFFIC: Autonomous Edge-AI Multi-Target Telemetry Platform</div>", unsafe_allow_html=True)
st.markdown("<div class='sub-title'>Edge YOLOv8 Neural Architecture &bull; ByteTrack Kalman-Filter Spatial Association &bull; Dynamic Trajectory Estimation & Line Flow Telemetry</div>", unsafe_allow_html=True)

# Placeholder KPI Metrics
kpi_col1, kpi_col2, kpi_col3, kpi_col4, kpi_col5 = st.columns(5)
kpi_active = kpi_col1.empty()
kpi_inbound = kpi_col2.empty()
kpi_outbound = kpi_col3.empty()
kpi_speed = kpi_col4.empty()
kpi_status = kpi_col5.empty()

# Initial KPI states
kpi_active.markdown("<div class='metric-card'><div class='metric-val'>00</div><div class='metric-lbl'>Active Targets</div></div>", unsafe_allow_html=True)
kpi_inbound.markdown("<div class='metric-card'><div class='metric-val'>00</div><div class='metric-lbl'>Total Inbound</div></div>", unsafe_allow_html=True)
kpi_outbound.markdown("<div class='metric-card'><div class='metric-val'>00</div><div class='metric-lbl'>Total Outbound</div></div>", unsafe_allow_html=True)
kpi_speed.markdown("<div class='metric-card'><div class='metric-val'>0.0</div><div class='metric-lbl'>Avg Speed (km/h)</div></div>", unsafe_allow_html=True)
kpi_status.markdown("<div class='metric-card'><div class='metric-val' style='color:#00e676;'>STANDBY</div><div class='metric-lbl'>Flow Status</div></div>", unsafe_allow_html=True)

st.markdown("---")

col_video, col_telemetry = st.columns([1.7, 1.0])

with col_video:
    st.markdown("#### Live Sensor Viewport & Trajectory Mapping")
    video_placeholder = st.empty()

with col_telemetry:
    st.markdown("#### Real-Time Target Telemetry Table")
    table_placeholder = st.empty()

# If user clicks Start or if pipeline is running
if run_pipeline:
    if video_path is None or not os.path.exists(video_path):
        st.error("Selected video feed could not be located. Please verify selection.")
        st.stop()
        
    tracker.reset_state()
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    
    frame_idx = 0
    processed_count = 0
    history_records = []
    
    # Progress Bar
    prog_bar = st.progress(0)
    
    while cap.isOpened() and processed_count < max_frames:
        ret, frame = cap.read()
        if not ret:
            break
            
        frame_idx += 1
        if frame_idx % frame_skip != 0:
            continue
            
        processed_count += 1
        prog_bar.progress(min(1.0, processed_count / max_frames))
        
        # Process Frame through YOLOv8 + ByteTrack
        annotated_frame, telemetry = tracker.process_frame(
            frame,
            conf_thresh=conf_val,
            allowed_classes=selected_classes,
            show_trails=show_trails,
            show_speed=show_speed,
            show_line=show_line,
            show_hud=show_hud,
            line_y_ratio=line_y_ratio
        )
        
        # Convert BGR to RGB for Streamlit canvas
        frame_rgb = cv2.cvtColor(annotated_frame, cv2.COLOR_BGR2RGB)
        video_placeholder.image(frame_rgb, use_container_width=True)
        
        # Update KPI Metric Cards
        st_color = "#00e676" if "FLUID" in telemetry["congestion_status"] else ("#ffab00" if "MODERATE" in telemetry["congestion_status"] else "#ff1744")
        kpi_active.markdown(f"<div class='metric-card'><div class='metric-val'>{telemetry['active_count']:02d}</div><div class='metric-lbl'>Active Targets</div></div>", unsafe_allow_html=True)
        kpi_inbound.markdown(f"<div class='metric-card'><div class='metric-val'>{telemetry['inbound_count']:02d}</div><div class='metric-lbl'>Total Inbound</div></div>", unsafe_allow_html=True)
        kpi_outbound.markdown(f"<div class='metric-card'><div class='metric-val'>{telemetry['outbound_count']:02d}</div><div class='metric-lbl'>Total Outbound</div></div>", unsafe_allow_html=True)
        kpi_speed.markdown(f"<div class='metric-card'><div class='metric-val'>{telemetry['avg_speed_kmh']:.1f}</div><div class='metric-lbl'>Avg Speed (km/h)</div></div>", unsafe_allow_html=True)
        kpi_status.markdown(f"<div class='metric-card'><div class='metric-val' style='color:{st_color};'>{telemetry['congestion_status']}</div><div class='metric-lbl'>Flow Status</div></div>", unsafe_allow_html=True)
        
        # Update Target Telemetry Table
        if telemetry["active_targets"]:
            df_targets = pd.DataFrame(telemetry["active_targets"])
            df_display = df_targets[["id", "class", "confidence", "speed_kmh", "trail_len"]].rename(
                columns={
                    "id": "Target ID",
                    "class": "Class",
                    "confidence": "Conf %",
                    "speed_kmh": "Speed (km/h)",
                    "trail_len": "History Points"
                }
            )
            table_placeholder.dataframe(df_display, use_container_width=True, height=260)
            
            for t in telemetry["active_targets"]:
                history_records.append({
                    "Frame": frame_idx,
                    "Target_ID": t["id"],
                    "Class": t["class"],
                    "Speed_KMH": t["speed_kmh"],
                    "Inbound_Total": telemetry["inbound_count"],
                    "Outbound_Total": telemetry["outbound_count"]
                })
        else:
            table_placeholder.info("Scanning sensor viewport... No active targets within detection threshold.")
            
        # Tiny delay to simulate natural video playback
        time.sleep(0.015)
        
    cap.release()
    prog_bar.empty()
    st.success(f"Execution Completed: Analyzed {processed_count} frames across selected video stream.")
    
    if history_records:
        df_export = pd.DataFrame(history_records)
        csv_data = df_export.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Export Surveillance Telemetry Audit Log (CSV)",
            data=csv_data,
            file_name=f"Traffic_Telemetry_Audit_{int(time.time())}.csv",
            mime="text/csv"
        )
else:
    # Display preview image when on standby
    preview_img = cv2.imread("report_assets/fig_telemetry_hud.png")
    if preview_img is not None:
        video_placeholder.image(cv2.cvtColor(preview_img, cv2.COLOR_BGR2RGB), use_container_width=True, caption="Surveillance Stream Standby: Press 'Start Video Tracking Pipeline' to stream live.")
    table_placeholder.info("Click '▶ Start Video Tracking Pipeline' in the sidebar to initiate real-time telemetry extraction.")

# ----------------- TECHNICAL & VIVA EXPLANATION -----------------
with st.expander("📘 Technical Architecture & Mathematical Formulations (Examiner Reference)"):
    st.markdown("""
    #### 1. Multi-Object Tracking (MOT) vs. Single-Frame Detection
    Standard object detection operates on isolated individual frames without temporal coherence. Multi-Object Tracking (MOT) associates detections across temporal sequences, maintaining persistent identity:
    $$\\mathcal{T}_t = \\{ (x_i, y_i, w_i, h_i, \\text{ID}_i) \\}_{i=1}^N$$
    ByteTrack combines the **Kalman Filter** for kinematic state prediction with the **Hungarian Algorithm** for optimal bipartite graph matching.

    #### 2. Kalman Filter State Estimation
    The motion of each tracked object is modeled via an 8-dimensional state vector:
    $$\\mathbf{x} = [u, v, \\gamma, h, \\dot{u}, \\dot{v}, \\dot{\\gamma}, \\dot{h}]^T$$
    Where $(u, v)$ is the 2D bounding box center, $\\gamma$ is the aspect ratio, $h$ is height, and their respective temporal velocity derivatives. The linear motion prediction step updates state and error covariance:
    $$\\hat{\\mathbf{x}}_{k|k-1} = \\mathbf{F} \\hat{\\mathbf{x}}_{k-1|k-1}$$
    $$\\mathbf{P}_{k|k-1} = \\mathbf{F} \\mathbf{P}_{k-1|k-1} \\mathbf{F}^T + \\mathbf{Q}$$

    #### 3. ByteTrack Low-Score Detection Association
    Unlike classical SORT or DeepSORT which discard low-confidence detections ($< 0.50$), ByteTrack retains all bounding boxes. Detections are partitioned into high-confidence ($\mathcal{D}_{\\text{high}}$) and low-confidence ($\mathcal{D}_{\\text{low}}$) sets:
    1. First association matches active tracklets with $\mathcal{D}_{\\text{high}}$ via Intersection-over-Union (IoU).
    2. Second association matches unmatched tracklets with $\mathcal{D}_{\\text{low}}$, successfully recovering occluded vehicles (e.g., cars behind poles or trees) without creating false identity switches.
    """)
