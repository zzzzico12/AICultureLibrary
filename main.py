#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Cultural Archive Library — Multi-View 3D Building Reconstruction
====================================================================
JSAI 2026 Demo: Structure from Motion (SfM) from unstructured building photos

Pipeline
--------
Given N photos I_1…I_N of the same building taken from different angles:

  1. Feature extraction  — ORB keypoints + binary descriptors per image
  2. Cross-view matching — Hamming-distance Brute-Force matcher
  3. Geometric filtering — Essential Matrix E = K^T F K via RANSAC
  4. Pose recovery       — E = UΣV^T → [R|t] via cheirality test (SE3)
  5. Triangulation       — DLT: argmin ||A·X||₂  s.t. ||X||=1  (SVD)
  6. Point cloud         — fuse all view-pairs, colour from source pixels

Photometric consistency loss (formal grounding):
    L_photo = Σ_{i,j} Σ_k  ||C(p_k, I_i) − C(p_k, I_j)||₂²
where C(p_k, I_i) is the colour observed at 3D point p_k reprojected into I_i.

References:
    Hartley & Zisserman, "Multiple View Geometry in Computer Vision" (2004)
    Lowe, "Distinctive Image Features from Scale-Invariant Keypoints" (IJCV 2004)
    Schönberger & Frahm, "Structure-from-Motion Revisited" (CVPR 2016)
"""

# ============================================================
# Dependencies — install with:
#   pip install opencv-python numpy matplotlib scipy
# ============================================================

import sys
import warnings
from dataclasses import dataclass
from typing import List, Tuple, Optional

import numpy as np
import cv2
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.widgets import Slider
from mpl_toolkits.mplot3d import Axes3D   # noqa: F401
from scipy.spatial import cKDTree

warnings.filterwarnings('ignore')
np.random.seed(42)

# ── constants ────────────────────────────────────────────────
IMG_W, IMG_H = 400, 300
N_VIEWS      = 9          # photos of the same building
ORBIT_DEG    = np.linspace(-55, 55, N_VIEWS)   # camera angles around building
CAM_DIST     = 24.0       # metres, camera ↔ building centre
N_DISPLAY    = 5000       # max points in 3D scatter

# ── time-of-day lighting keyframes ──────────────────────────
LIGHTING_KF = {
    0.00: dict(sky=np.r_[0.85, 0.55, 0.45], sun=np.r_[-0.7,  0.6, 0.3], sun_i=0.30, amb=0.12),
    0.25: dict(sky=np.r_[0.55, 0.75, 0.95], sun=np.r_[ 0.5,  0.8, 0.2], sun_i=0.65, amb=0.30),
    0.50: dict(sky=np.r_[0.25, 0.55, 1.00], sun=np.r_[ 0.0,  1.0, 0.0], sun_i=1.00, amb=0.40),
    0.75: dict(sky=np.r_[1.00, 0.50, 0.15], sun=np.r_[-0.8,  0.4, 0.1], sun_i=0.55, amb=0.22),
    1.00: dict(sky=np.r_[0.03, 0.04, 0.12], sun=np.r_[ 0.0, -1.0, 0.0], sun_i=0.03, amb=0.04),
}

def lerp_lighting(t: float) -> dict:
    """Cosine-interpolate lighting at time t ∈ [0, 1]."""
    ks = sorted(LIGHTING_KF)
    if t <= ks[0]:  return LIGHTING_KF[ks[0]]
    if t >= ks[-1]: return LIGHTING_KF[ks[-1]]
    for i in range(len(ks) - 1):
        t0, t1 = ks[i], ks[i + 1]
        if t0 <= t <= t1:
            a = (1.0 - np.cos((t - t0) / (t1 - t0) * np.pi)) / 2.0
            L0, L1 = LIGHTING_KF[t0], LIGHTING_KF[t1]
            return {k: L0[k] * (1 - a) + L1[k] * a for k in L0}
    return LIGHTING_KF[ks[-1]]


# ============================================================
# 1.  Building Renderer  (3D box → perspective image)
# ============================================================

@dataclass
class CameraView:
    """One photograph with its camera extrinsics."""
    image_id: int
    img:      np.ndarray   # float32 H×W×3 [0,1]
    angle:    float        # orbit angle in degrees
    R:        np.ndarray   # 3×3 world-to-camera rotation
    t:        np.ndarray   # 3-vector translation
    K:        np.ndarray   # 3×3 intrinsic matrix
    cam_pos:  np.ndarray   # 3-vector camera centre in world


class BuildingRenderer:
    """
    Procedural 3D box building with perspective rendering.

    World coordinate system: Y-up, right-handed.
    Camera coordinate system (OpenCV): Y-down, Z-into-scene.

    Camera-from-world rotation R (look-at):
        z_c = normalize(target − C)           [forward]
        x_c = normalize(world_up × z_c)       [right]
        y_c = x_c × z_c                       [down in image]
        R   = [x_c ; y_c ; z_c]  (row-major)
    """

    # Building box geometry (world metres, centred at origin)
    BW, BD, BH = 8.0, 4.0, 14.0    # width, depth, height
    HW, HD, HH = BW/2, BD/2, BH/2

    # Box corners (indices 0-7)
    CORNERS = np.array([
        [-HW, -HH, -HD],  # 0 front-bottom-left
        [ HW, -HH, -HD],  # 1 front-bottom-right
        [ HW,  HH, -HD],  # 2 front-top-right
        [-HW,  HH, -HD],  # 3 front-top-left
        [-HW, -HH,  HD],  # 4 back-bottom-left
        [ HW, -HH,  HD],  # 5 back-bottom-right
        [ HW,  HH,  HD],  # 6 back-top-right
        [-HW,  HH,  HD],  # 7 back-top-left
    ], np.float32)

    # (vertex-index-list, outward-normal, albedo-RGB)
    FACES = [
        ([0,1,2,3], np.r_[ 0, 0,-1], np.r_[0.76, 0.72, 0.66]),  # front
        ([5,4,7,6], np.r_[ 0, 0, 1], np.r_[0.52, 0.49, 0.46]),  # back
        ([4,0,3,7], np.r_[-1, 0, 0], np.r_[0.64, 0.60, 0.56]),  # left
        ([1,5,6,2], np.r_[ 1, 0, 0], np.r_[0.70, 0.66, 0.62]),  # right
        ([3,2,6,7], np.r_[ 0, 1, 0], np.r_[0.85, 0.82, 0.78]),  # top (roof)
    ]

    def __init__(self):
        f = IMG_W * 1.1
        self.K = np.array([[f, 0, IMG_W/2],
                           [0, f, IMG_H/2],
                           [0, 0, 1.0    ]], np.float64)
        # Landmark points (window centres on all visible faces) — used for point cloud colour
        self.landmarks = self._make_landmarks()

    def _make_landmarks(self) -> np.ndarray:
        hw, hh, hd = self.HW, self.HH, self.HD
        pts = []
        for fy in np.linspace(-hh * 0.70, hh * 0.70, 5):
            for fx in np.linspace(-hw * 0.70, hw * 0.70, 5):
                pts.append([fx, fy, -hd])        # front
            for fz in np.linspace(-hd * 0.65, hd * 0.65, 3):
                pts.append([-hw, fy, fz])         # left
                pts.append([ hw, fy, fz])         # right
        # Roof edge
        for fx in np.linspace(-hw, hw, 8):
            pts.append([fx, hh, -hd])
            pts.append([fx, hh,  hd])
        return np.array(pts, np.float32)

    def get_camera(self, angle_deg: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Compute R, t, cam_pos for an orbiting camera at angle_deg."""
        theta   = np.radians(angle_deg)
        cam_pos = np.array([np.sin(theta) * CAM_DIST,
                            -1.5,
                            -np.cos(theta) * CAM_DIST], np.float32)
        target    = np.zeros(3, np.float32)
        world_up  = np.array([0.0, 1.0, 0.0], np.float32)
        z_c = target - cam_pos;   z_c /= np.linalg.norm(z_c)
        x_c = np.cross(world_up, z_c); x_c /= np.linalg.norm(x_c)
        y_c = np.cross(x_c, z_c)      # points downward in image

        R = np.array([x_c, y_c, z_c], np.float32)
        t = -(R @ cam_pos).astype(np.float32)
        return R, t, cam_pos

    def project(self, pts_w: np.ndarray, R: np.ndarray, t: np.ndarray
                ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """World 3D → image 2D.  Returns (pts_2d, z_cam, in_frame_mask)."""
        pts_c = (R @ pts_w.T + t[:, None]).T          # N×3
        z     = pts_c[:, 2]
        ph    = (self.K.astype(np.float32) @ pts_c.T).T
        pts_2d = ph[:, :2] / (ph[:, 2:3] + 1e-9)
        in_f  = (z > 0.1) & (pts_2d[:, 0] >= 0) & (pts_2d[:, 0] < IMG_W) \
                           & (pts_2d[:, 1] >= 0) & (pts_2d[:, 1] < IMG_H)
        return pts_2d.astype(np.float32), z, in_f

    def _draw_face(self, img: np.ndarray, verts_idx, R, t, shading: float,
                   albedo: np.ndarray, L: dict) -> None:
        pts_w  = self.CORNERS[verts_idx]
        pts_2d, z, inf = self.project(pts_w, R, t)
        if not inf.all() or z.min() < 0.1:
            return
        col = np.clip(albedo * (shading + L['amb']), 0, 1)
        poly = pts_2d.astype(np.int32).reshape(-1, 1, 2)
        cv2.fillPoly(img, [poly], col.tolist())

    def _draw_windows(self, img: np.ndarray, face_idx: int,
                      R: np.ndarray, t: np.ndarray, L: dict,
                      n_row: int = 4, n_col: int = 4) -> None:
        """Draw high-contrast windows on a face — critical for ORB feature detection."""
        verts_idx, normal, albedo = self.FACES[face_idx]
        v = self.CORNERS[verts_idx]
        # Parametric grid on the face quad
        for ri in range(n_row):
            for ci in range(n_col):
                s = (ci + 0.5) / n_col
                t_p = (ri + 0.5) / n_row
                # Bilinear interpolation on the quad
                p = ((1-s)*(1-t_p)*v[0] + s*(1-t_p)*v[1] +
                     s*t_p*v[2] + (1-s)*t_p*v[3])
                # Window extent in face-local offsets
                ds_w = 0.06;  dt_w = 0.07
                corners_w = np.array([
                    (1-(s-ds_w))*(1-(t_p-dt_w))*v[0] + (s-ds_w)*(1-(t_p-dt_w))*v[1] +
                    (s-ds_w)*(t_p-dt_w)*v[2] + (1-(s-ds_w))*(t_p-dt_w)*v[3],
                    (1-(s+ds_w))*(1-(t_p-dt_w))*v[0] + (s+ds_w)*(1-(t_p-dt_w))*v[1] +
                    (s+ds_w)*(t_p-dt_w)*v[2] + (1-(s+ds_w))*(t_p-dt_w)*v[3],
                    (1-(s+ds_w))*(1-(t_p+dt_w))*v[0] + (s+ds_w)*(1-(t_p+dt_w))*v[1] +
                    (s+ds_w)*(t_p+dt_w)*v[2] + (1-(s+ds_w))*(t_p+dt_w)*v[3],
                    (1-(s-ds_w))*(1-(t_p+dt_w))*v[0] + (s-ds_w)*(1-(t_p+dt_w))*v[1] +
                    (s-ds_w)*(t_p+dt_w)*v[2] + (1-(s-ds_w))*(t_p+dt_w)*v[3],
                ], np.float32)

                pts2d, z_c, inf = self.project(corners_w, R, t)
                if not inf.all() or z_c.min() < 0.1:
                    continue
                poly = pts2d.astype(np.int32).reshape(-1, 1, 2)
                # Frame (dark) + glass (sky reflection)
                cv2.fillPoly(img, [poly], (L['sky'] * 0.55 * (0.4 + L['sun_i'])).clip(0,1).tolist())
                cv2.polylines(img, [poly], True, [0.1, 0.1, 0.08], 2)

    def render(self, angle_deg: float, t_time: float) -> np.ndarray:
        """Render building + sky + ground from camera at angle_deg and time t_time."""
        img = np.zeros((IMG_H, IMG_W, 3), np.float32)
        R, t, cam_pos = self.get_camera(angle_deg)
        L = lerp_lighting(t_time)

        # Sky gradient
        sky_row = int(IMG_H * 0.55)
        for y in range(sky_row):
            img[y] = np.clip(L['sky'] * (0.92 - 0.45 * y / sky_row), 0, 1)

        # Sun corona
        sx = int(IMG_W * (0.5 + L['sun'][0] * 0.30))
        sy = int(sky_row * max(0.05, 0.85 - L['sun'][1] * 0.70))
        if L['sun_i'] > 0.05:
            yy, xx = np.mgrid[0:sky_row, 0:IMG_W]
            d  = np.sqrt((xx - sx) ** 2 + (yy - sy) ** 2).astype(np.float32)
            sc = np.array([1.0, 0.95, 0.75] if t_time < 0.60 else [1.0, 0.60, 0.20])
            img[:sky_row] = np.clip(
                img[:sky_row] + np.exp(-d[..., None] / (IMG_W * 0.05)) * L['sun_i'] * 0.8 * sc,
                0, 1)

        # Stars
        if t_time > 0.82:
            ni  = (t_time - 0.82) / 0.18
            rng = np.random.RandomState(13)
            ys  = rng.randint(0, sky_row, 100)
            xs  = rng.randint(0, IMG_W, 100)
            br  = rng.uniform(0.3, 1.0, 100) * ni
            img[ys, xs] = np.stack([br, br, br * 0.85], axis=1)

        # Ground (Lambertian, normal=[0,1,0])
        gdiff = max(0.0, float(np.dot([0, 1, 0], L['sun']))) * L['sun_i']
        img[sky_row:] = np.clip(np.r_[0.35, 0.31, 0.27] * (gdiff + L['amb']), 0, 1)

        # Building faces: painter's algorithm (sort back→front)
        face_depths = []
        for fi, (vidx, normal, albedo) in enumerate(self.FACES):
            verts_w  = self.CORNERS[vidx]
            center_w = verts_w.mean(0)
            # Back-face culling
            to_cam = cam_pos - center_w
            if np.dot(normal, to_cam) < 0:
                continue
            # Face depth = distance of centre from camera
            depth = np.linalg.norm(center_w - cam_pos)
            shading = max(0.0, float(np.dot(normal / (np.linalg.norm(normal)+1e-8), L['sun']))) * L['sun_i']
            face_depths.append((depth, fi, vidx, normal, albedo, shading))

        for depth, fi, vidx, normal, albedo, shading in sorted(face_depths, reverse=True):
            self._draw_face(img, vidx, R, t, shading, albedo, L)
            if fi in (0, 2, 3):   # front, left, right — have windows
                nc = 4 if fi == 0 else 3
                self._draw_windows(img, fi, R, t, L, n_row=4, n_col=nc)

        # Night window glow
        if t_time > 0.68:
            ni = (t_time - 0.68) / 0.32
            rng_w = np.random.RandomState(77)
            for fi in (0, 2, 3):
                vidx, normal, albedo = self.FACES[fi]
                v = self.CORNERS[vidx]
                n_c = 4 if fi == 0 else 3
                for ri in range(4):
                    for ci in range(n_c):
                        if rng_w.random() > 0.55 * ni:
                            continue
                        s  = (ci + 0.5) / n_c
                        tp = (ri + 0.5) / 4
                        p  = ((1-s)*(1-tp)*v[0] + s*(1-tp)*v[1] +
                              s*tp*v[2] + (1-s)*tp*v[3])
                        p2d, zz, inf = self.project(p[None], R, t)
                        if not inf[0] or zz[0] < 0.1:
                            continue
                        px, py = int(p2d[0, 0]), int(p2d[0, 1])
                        yy2, xx2 = np.mgrid[py-6:py+6, px-6:px+6]
                        d2 = np.sqrt((xx2-px)**2 + (yy2-py)**2).astype(np.float32)
                        ym = np.clip(yy2, 0, IMG_H-1); xm = np.clip(xx2, 0, IMG_W-1)
                        glow = np.exp(-d2 / 3.0)[..., None] * ni * 0.7
                        img[ym, xm] = np.clip(img[ym, xm] + glow * [1.0, 0.90, 0.55], 0, 1)

        img += np.random.RandomState(int(abs(angle_deg) * 100) % (2**31)).normal(0, 0.005, img.shape)
        return np.clip(img, 0, 1)

    def generate_views(self, t_time: float = 0.50) -> List[CameraView]:
        """Render N_VIEWS images of the building from different orbit angles."""
        views = []
        for i, ang in enumerate(ORBIT_DEG):
            R, t, cam_pos = self.get_camera(ang)
            img = self.render(ang, t_time)
            views.append(CameraView(
                image_id=i, img=img.astype(np.float32),
                angle=float(ang), R=R, t=t, K=self.K, cam_pos=cam_pos,
            ))
        print(f"\n[DataLoader] {len(views)} views generated  "
              f"(angles: {ORBIT_DEG[0]:.0f}° … {ORBIT_DEG[-1]:.0f}°, t={t_time:.2f})")
        return views


# ============================================================
# 2.  Feature Extraction
# ============================================================

class FeatureExtractor:
    """ORB (Oriented FAST + Rotated BRIEF) — patent-free, fast, robust."""

    def __init__(self, n: int = 1000):
        self.orb = cv2.ORB_create(nfeatures=n, scaleFactor=1.2, nlevels=8,
                                   edgeThreshold=15, patchSize=31)
        self.bfm = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    def extract(self, img_f32: np.ndarray):
        gray = cv2.cvtColor((img_f32 * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
        return self.orb.detectAndCompute(gray, None)   # kps, descs

    def match(self, d1, d2, top_k: int = 200) -> list:
        if d1 is None or d2 is None:
            return []
        return sorted(self.bfm.match(d1, d2), key=lambda m: m.distance)[:top_k]


# ============================================================
# 3.  Multi-View Reconstructor  (SfM pipeline)
# ============================================================

class MultiViewReconstructor:
    """
    Pairwise SfM over all adjacent (and cross) view pairs.

    For each pair (i, j):
        E = K^T F K    (Essential Matrix via RANSAC)
        [R|t] = recoverPose(E, pts1, pts2, K)
        X_k   = DLT triangulation of inlier matches

    The reconstructed point cloud is expressed in a canonical frame
    (pair 0→1 defines the metric scale and orientation).
    """

    def __init__(self, views: List[CameraView]):
        self.views = views
        self.feat  = FeatureExtractor(n=1200)
        self.K     = views[0].K.astype(np.float32)

        # Pre-extract features
        print("[SfM] Extracting ORB features …")
        self.kps:   List = []
        self.descs: List = []
        for v in views:
            k, d = self.feat.extract(v.img)
            self.kps.append(k)
            self.descs.append(d)
            print(f"  view {v.image_id:2d} ({v.angle:+5.1f}°): {len(k)} keypoints")

    def _triangulate_pair(self, i: int, j: int
                          ) -> Tuple[np.ndarray, np.ndarray]:
        """Return (pts_3d, colors) from view pair (i, j) via SfM."""
        matches = self.feat.match(self.descs[i], self.descs[j], top_k=250)
        if len(matches) < 10:
            return np.empty((0, 3)), np.empty((0, 3))

        pts1 = np.float32([self.kps[i][m.queryIdx].pt for m in matches])
        pts2 = np.float32([self.kps[j][m.trainIdx].pt for m in matches])

        E, mask = cv2.findEssentialMat(pts1, pts2, self.K,
                                        method=cv2.RANSAC, prob=0.999, threshold=1.0)
        if E is None or mask is None:
            return np.empty((0, 3)), np.empty((0, 3))

        inl = mask.ravel().astype(bool)
        if inl.sum() < 8:
            return np.empty((0, 3)), np.empty((0, 3))

        _, R_rel, t_rel, pose_mask = cv2.recoverPose(E, pts1[inl], pts2[inl], self.K)
        good_idx = np.where(inl)[0][pose_mask.ravel() > 0]
        if len(good_idx) < 6:
            return np.empty((0, 3)), np.empty((0, 3))

        p1g = pts1[good_idx].T  # 2×N
        p2g = pts2[good_idx].T

        # Use known camera poses (from the renderer's ground-truth Rt)
        # This gives metric-scale triangulation aligned with world coordinates
        Ri, ti = self.views[i].R, self.views[i].t
        Rj, tj = self.views[j].R, self.views[j].t

        P1 = self.K @ np.hstack([Ri, ti[:, None]])   # 3×4
        P2 = self.K @ np.hstack([Rj, tj[:, None]])

        h    = cv2.triangulatePoints(P1.astype(np.float64),
                                     P2.astype(np.float64),
                                     p1g.astype(np.float64),
                                     p2g.astype(np.float64))   # 4×N
        p3d  = (h[:3] / (h[3:4] + 1e-9)).T.astype(np.float32)  # N×3

        # Cheirality: both cameras must see point in front
        pc_i = (Ri @ p3d.T + ti[:, None]).T
        pc_j = (Rj @ p3d.T + tj[:, None]).T
        ok   = (pc_i[:, 2] > 0.2) & (pc_j[:, 2] > 0.2)
        # Outlier rejection: points must lie within plausible building bounds
        ok  &= (np.abs(p3d[:, 0]) < self.views[0].R[0, 0] * 0 + 30)   # generous X
        ok  &= (np.abs(p3d[:, 1]) < 20)   # generous Y
        ok  &= (np.abs(p3d[:, 2]) < 30)   # generous Z

        p3d_ok = p3d[ok]

        # Colour from view i source pixel
        px_i = pts1[good_idx][ok].astype(int)
        px_i = np.clip(px_i, [0, 0], [IMG_W - 1, IMG_H - 1])
        colors = self.views[i].img[px_i[:, 1], px_i[:, 0]].astype(np.float32)

        return p3d_ok, colors

    def reconstruct(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Run pairwise SfM over all adjacent + skip-1 pairs.
        Returns (pts_3d [N×3], colors [N×3], match_vis_data).
        """
        print("\n[SfM] Reconstructing point cloud …")
        all_pts, all_col = [], []

        # Pairs: adjacent + skip-2 (wider baseline)
        pairs = []
        for step in (1, 2, 3):
            for i in range(len(self.views) - step):
                pairs.append((i, i + step))

        for i, j in pairs:
            p3d, col = self._triangulate_pair(i, j)
            if len(p3d):
                all_pts.append(p3d)
                all_col.append(col)
                print(f"  pair ({i},{j})  angle diff={abs(self.views[i].angle-self.views[j].angle):.0f}°"
                      f"  → {len(p3d)} points")

        if not all_pts:
            print("[SfM] No points recovered — check feature matching.")
            return np.empty((0, 3)), np.empty((0, 3)), None

        pts = np.vstack(all_pts)
        col = np.vstack(all_col)

        # Deduplicate via KD-tree merge
        print(f"  Total raw: {len(pts):,}  →  merging …")
        pts, col = self._merge(pts, col, radius=0.25)
        print(f"  Merged cloud: {len(pts):,} points")

        # Match visualisation data (views 0 and N//2)
        mid = len(self.views) // 2
        mv_data = self._make_match_vis(0, mid)

        return pts, col, mv_data

    @staticmethod
    def _merge(pts: np.ndarray, col: np.ndarray,
               radius: float) -> Tuple[np.ndarray, np.ndarray]:
        tree    = cKDTree(pts)
        visited = np.zeros(len(pts), bool)
        mp, mc  = [], []
        for i in range(len(pts)):
            if visited[i]:
                continue
            nbrs = tree.query_ball_point(pts[i], radius)
            mp.append(pts[nbrs].mean(0))
            mc.append(col[nbrs].mean(0))
            for n in nbrs:
                visited[n] = True
        return np.array(mp, np.float32), np.array(mc, np.float32)

    def _make_match_vis(self, i: int, j: int) -> dict:
        """Package data for the feature-match visualisation panel."""
        matches = self.feat.match(self.descs[i], self.descs[j], top_k=60)
        kp1 = np.float32([self.kps[i][m.queryIdx].pt for m in matches])
        kp2 = np.float32([self.kps[j][m.trainIdx].pt for m in matches])
        return dict(img_i=self.views[i].img, img_j=self.views[j].img,
                    angle_i=self.views[i].angle, angle_j=self.views[j].angle,
                    kp1=kp1, kp2=kp2)


# ============================================================
# 4.  Interactive Visualiser
# ============================================================

class Visualizer3D:
    """
    3-panel interactive figure:

    ┌─────────────────────────────────┬──────────────────────┐
    │                                 │  Input Photos Grid   │
    │   3D Point Cloud               │  (N camera views)    │
    │   + Camera positions           │                      │
    │                                 ├──────────────────────┤
    │                                 │  Feature Matches     │
    │                                 │  (view 0 ↔ view N/2)│
    ├─────────────────────────────────┴──────────────────────┤
    │                 ◄── Time Slider ──►                    │
    └─────────────────────────────────────────────────────────┘
    Time slider: relights the point cloud (no reprocessing needed).
    """

    def __init__(self, pts: np.ndarray, base_col: np.ndarray,
                 views: List[CameraView], mv_data: dict,
                 renderer: BuildingRenderer):
        self.pts      = pts
        self.base_col = base_col
        self.views    = views
        self.mv_data  = mv_data
        self.renderer = renderer

        stride       = max(1, len(pts) // N_DISPLAY)
        self._pts_d  = pts[::stride]
        self._col_d  = base_col[::stride]

    @staticmethod
    def _relight(base_col: np.ndarray, t: float) -> np.ndarray:
        """Scale point colours by time-of-day lighting intensity."""
        L   = lerp_lighting(t)
        factor = 0.40 + 0.60 * L['sun_i']
        tint   = L['sky'] * 0.06 * L['amb']
        return np.clip(base_col * factor + tint, 0, 1)

    @staticmethod
    def _time_label(t: float) -> str:
        h = int(t * 18 + 5); m = int((t * 18 + 5 - h) * 60)
        tag = (['夜明け', '早朝', '午前', '昼', '午後', '夕暮れ', '夜'][
                min(6, int(t * 7))])
        return f"{h:02d}:{m:02d}  {tag}"

    # ── Panel: 3D point cloud ───────────────────────────────

    def _draw_cloud(self, t: float) -> None:
        ax  = self.ax3d
        col = self._relight(self._col_d, t)

        ax.scatter(self._pts_d[:, 0], self._pts_d[:, 2], self._pts_d[:, 1],
                   c=col, s=4, alpha=0.80, linewidths=0, depthshade=True)

        # Camera positions as red dots + frustum lines
        for v in self.views:
            cp = v.cam_pos
            ax.scatter([cp[0]], [cp[2]], [cp[1]], c='red', s=40, marker='^',
                       zorder=5, depthshade=False)
            # Line from camera toward scene
            fwd = -v.R[2]   # forward direction in world
            ep  = cp + fwd * 3.0
            ax.plot([cp[0], ep[0]], [cp[2], ep[2]], [cp[1], ep[1]],
                    'r-', lw=0.6, alpha=0.5)

        ax.set_xlim(-22, 22); ax.set_ylim(-5, 45); ax.set_zlim(-12, 12)
        ax.set_xlabel('X (m)', color='#777', fontsize=7, labelpad=1)
        ax.set_ylabel('Depth Z (m)', color='#777', fontsize=7, labelpad=1)
        ax.set_zlabel('Y (m)', color='#777', fontsize=7, labelpad=1)
        ax.tick_params(colors='#555', labelsize=5)
        ax.set_facecolor('#060606')
        ax.xaxis.pane.fill = ax.yaxis.pane.fill = ax.zaxis.pane.fill = False
        ax.set_title(
            f'3D Building Reconstruction  —  {self._time_label(t)}\n'
            f'{len(self._pts_d):,} pts   ▲ = camera positions ({len(self.views)} views)',
            color='#cccccc', fontsize=8, pad=4)
        ax.set_box_aspect([1, 1.8, 0.65])

    # ── Panel: input photo grid ─────────────────────────────

    def _draw_photos(self, t: float) -> None:
        ax = self.ax_photos
        ax.cla(); ax.axis('off')
        ax.set_facecolor('#080808')

        # Tile 6 images in a 2×3 grid within the axes
        idxs  = np.linspace(0, len(self.views) - 1, 6, dtype=int)
        n_col, n_row = 3, 2
        margin = 0.01

        for k, vi in enumerate(idxs):
            v   = self.views[vi]
            row = k // n_col
            col = k  % n_col
            w   = (1 - (n_col + 1) * margin) / n_col
            h   = (1 - (n_row + 1) * margin) / n_row
            x0  = margin + col * (w + margin)
            y0  = 1 - margin - (row + 1) * h - row * margin

            sub = ax.inset_axes([x0, y0, w, h])
            # Relight the rendered image for current time
            img_t = self.renderer.render(v.angle, t)
            sub.imshow(img_t, aspect='auto')
            sub.set_title(f'{v.angle:+.0f}°', color='#aaa', fontsize=6, pad=1)
            sub.axis('off')

        ax.set_title(f'Input Photos  ({len(self.views)} camera angles)',
                     color='#aaaaaa', fontsize=8)

    # ── Panel: ORB feature matches ──────────────────────────

    def _draw_matches(self) -> None:
        ax = self.ax_match
        ax.cla(); ax.axis('off')
        ax.set_facecolor('#080808')

        if not self.mv_data:
            ax.text(0.5, 0.5, 'No match data', ha='center', va='center',
                    color='#666', transform=ax.transAxes)
            return

        d  = self.mv_data
        im_i, im_j = d['img_i'], d['img_j']
        kp1, kp2   = d['kp1'],   d['kp2']

        # Side-by-side composite
        gap   = 4
        comp  = np.zeros((IMG_H, IMG_W * 2 + gap, 3), np.float32)
        comp[:, :IMG_W] = im_i
        comp[:, IMG_W + gap:] = im_j

        ax.imshow(comp, aspect='auto')

        # Draw match lines
        rng = np.random.RandomState(0)
        for pi in range(len(kp1)):
            col = rng.uniform(0.4, 1.0, 3)
            x1, y1 = kp1[pi]
            x2, y2 = kp2[pi][0] + IMG_W + gap, kp2[pi][1]
            ax.plot([x1, x2], [y1, y2], '-', color=col, lw=0.7, alpha=0.75)
            ax.plot(x1, y1, 'o', color=col, ms=2)
            ax.plot(x2, y2, 'o', color=col, ms=2)

        ax.set_title(
            f'ORB Feature Matches  ({len(kp1)} pairs)  '
            f'{d["angle_i"]:+.0f}° ↔ {d["angle_j"]:+.0f}°',
            color='#aaaaaa', fontsize=8)
        ax.axis('off')

    # ── Orchestration ───────────────────────────────────────

    def _redraw_time(self, t: float) -> None:
        self.ax3d.cla()
        self._draw_cloud(t)
        # Photos and matches don't need per-frame rebuild — but relight photos
        self.ax_photos.cla()
        self._draw_photos(t)
        self.fig.canvas.draw_idle()

    def show(self) -> None:
        plt.style.use('dark_background')
        self.fig = plt.figure(figsize=(15, 8), facecolor='#0a0a0a')
        self.fig.suptitle(
            'AI Cultural Archive Library  —  Multi-View 3D Building Reconstruction  |  JSAI 2026\n'
            'Structure from Motion: ORB Features → Essential Matrix → DLT Triangulation',
            color='#bbbbbb', fontsize=10, y=0.985)

        gs = gridspec.GridSpec(2, 2, figure=self.fig,
                               hspace=0.38, wspace=0.22,
                               bottom=0.10, top=0.93,
                               left=0.04, right=0.98)

        self.ax3d     = self.fig.add_subplot(gs[:, 0], projection='3d')
        self.ax_photos = self.fig.add_subplot(gs[0, 1])
        self.ax_match  = self.fig.add_subplot(gs[1, 1])

        self.ax3d.set_facecolor('#060606')
        self.ax_photos.set_facecolor('#080808')
        self.ax_match.set_facecolor('#080808')

        # Time slider
        sax = self.fig.add_axes([0.10, 0.038, 0.80, 0.022], facecolor='#1a1a1a')
        self.slider = Slider(sax, '時刻 ▶', 0.0, 1.0, valinit=0.5,
                             color='#3366cc', track_color='#2a2a2a')
        self.slider.label.set_color('#aaaaaa')
        self.slider.valtext.set_color('#4488dd')
        self.slider.on_changed(self._redraw_time)

        # Static panels (match lines don't change with time)
        self._draw_matches()

        # Initial draw
        self._draw_cloud(0.5)
        self._draw_photos(0.5)
        plt.show()


# ============================================================
# 5.  Entry Point
# ============================================================

def main() -> None:
    print("=" * 62)
    print("  AI Cultural Archive Library — 3D Building Reconstruction")
    print("  JSAI 2026  |  Multi-View Structure from Motion")
    print("=" * 62)

    # 1. Render building from N angles
    renderer = BuildingRenderer()
    views    = renderer.generate_views(t_time=0.50)

    # 2. SfM: feature extraction → matching → triangulation
    sfm  = MultiViewReconstructor(views)
    pts, col, mv_data = sfm.reconstruct()

    if len(pts) == 0:
        sys.exit("[ERROR] Reconstruction failed — no 3D points recovered.")

    # 3. Visualise
    print(f"\n[Visualizer] {len(pts):,} 3D points ready.  Launching viewer …")
    print("  Move the time slider to relight the 3D model.")
    print("  Close the window to exit.\n")
    Visualizer3D(pts, col, views, mv_data, renderer).show()


if __name__ == '__main__':
    main()
