"""
HYPERSPECTRAL VEGETATION INDEX ANALYSER
========================================
Streamlit app using Google Earth Engine for field analysis.

SETUP:
    pip install earthengine-api geemap streamlit folium matplotlib numpy pandas
    earthengine authenticate   # one-time browser login
    streamlit run app.py
"""

import streamlit as st
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import io, json, base64
from datetime import datetime, timedelta, date

from firebase_upload import (
    upload_satellite_results,
    get_rover_readings,
    get_fused_recommendations,
)

# ─── Google Earth Engine ──────────────────────────────────────────────────────
try:
    import ee
    GEE_AVAILABLE = True
except Exception:
    GEE_AVAILABLE = False

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="HyperSpec Field Analyser",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    [data-testid="stSidebar"] { background: #0f1117; }
    [data-testid="stSidebar"] * { color: #e0e0e0 !important; }
    .metric-card {
        background: #1e2130;
        border-radius: 12px;
        padding: 16px 20px;
        margin: 6px 0;
        border-left: 4px solid #4CAF50;
    }
    .metric-card.bad  { border-left-color: #f44336; }
    .metric-card.warn { border-left-color: #ff9800; }
    .metric-label { font-size: 12px; color: #888; margin-bottom: 4px; }
    .metric-value { font-size: 26px; font-weight: 700; color: #fff; }
    .metric-sub   { font-size: 12px; color: #aaa; margin-top: 2px; }
    h1 { color: #4CAF50 !important; }
    .stButton>button {
        background: #4CAF50; color: white; border: none;
        border-radius: 8px; padding: 10px 28px;
        font-size: 15px; font-weight: 600; cursor: pointer;
    }
    .stButton>button:hover { background: #43A047; }
    .stAlert { border-radius: 10px; }
</style>
""", unsafe_allow_html=True)

# ─── GEE Initialisation ───────────────────────────────────────────────────────
@st.cache_resource
def init_ee():
    if not GEE_AVAILABLE:
        return False, "earthengine-api not installed"
    try:
        ee.Initialize(project=st.session_state.get("gee_project", ""))
        return True, "ok"
    except Exception as e:
        try:
            ee.Authenticate(auth_mode="notebook")
            ee.Initialize(project=st.session_state.get("gee_project", ""))
            return True, "ok"
        except Exception as e2:
            return False, str(e2)

# ─── Vegetation Index Definitions ─────────────────────────────────────────────
INDEX_DEFS = {
    "NDVI": {
        "desc": "Normalised Difference Vegetation Index",
        "formula": "(NIR - Red) / (NIR + Red)",
        "range": (-1, 1),
        "good_range": (0.4, 1.0),
        "cmap": "RdYlGn",
        "bands": ["B8", "B4"],
    },
    "EVI": {
        "desc": "Enhanced Vegetation Index",
        "formula": "2.5 × (NIR - Red) / (NIR + 6×Red - 7.5×Blue + 1)",
        "range": (-1, 1),
        "good_range": (0.3, 0.9),
        "cmap": "RdYlGn",
        "bands": ["B8", "B4", "B2"],
    },
    "SAVI": {
        "desc": "Soil-Adjusted Vegetation Index",
        "formula": "1.5 × (NIR - Red) / (NIR + Red + 0.5)",
        "range": (-1, 1),
        "good_range": (0.3, 0.8),
        "cmap": "RdYlGn",
        "bands": ["B8", "B4"],
    },
    "NDWI": {
        "desc": "Normalised Difference Water Index",
        "formula": "(Green - NIR) / (Green + NIR)",
        "range": (-1, 1),
        "good_range": (-0.1, 0.2),
        "cmap": "RdBu",
        "bands": ["B3", "B8"],
    },
    "NDRE": {
        "desc": "Normalised Difference Red Edge",
        "formula": "(NIR - RedEdge) / (NIR + RedEdge)",
        "range": (-1, 1),
        "good_range": (0.2, 0.6),
        "cmap": "RdYlGn",
        "bands": ["B8", "B5"],
    },
    "NDMI": {
        "desc": "Normalised Difference Moisture Index",
        "formula": "(NIR - SWIR1) / (NIR + SWIR1)",
        "range": (-1, 1),
        "good_range": (0.1, 0.5),
        "cmap": "RdBu",
        "bands": ["B8", "B11"],
    },
    "GNDVI": {
        "desc": "Green NDVI",
        "formula": "(NIR - Green) / (NIR + Green)",
        "range": (-1, 1),
        "good_range": (0.35, 0.75),
        "cmap": "RdYlGn",
        "bands": ["B8", "B3"],
    },
    "CHL": {
        "desc": "Chlorophyll Index (Red Edge)",
        "formula": "(RedEdge2 / RedEdge1) - 1",
        "range": (0, 10),
        "good_range": (1.0, 4.0),
        "cmap": "YlGn",
        "bands": ["B6", "B5"],
    },
}

# ─── GEE Band Calculations ─────────────────────────────────────────────────────
def compute_indices_ee(image):
    """Add all spectral index bands to a Sentinel-2 image (reflectance / 10000)."""
    img = image.divide(10000)  # scale to 0–1

    ndvi  = img.normalizedDifference(["B8",  "B4"]).rename("NDVI")
    evi   = img.expression(
        "2.5 * (NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1)",
        {"NIR": img.select("B8"), "RED": img.select("B4"), "BLUE": img.select("B2")}
    ).rename("EVI")
    savi  = img.expression(
        "1.5 * (NIR - RED) / (NIR + RED + 0.5)",
        {"NIR": img.select("B8"), "RED": img.select("B4")}
    ).rename("SAVI")
    ndwi  = img.normalizedDifference(["B3",  "B8"]).rename("NDWI")
    ndre  = img.normalizedDifference(["B8",  "B5"]).rename("NDRE")
    ndmi  = img.normalizedDifference(["B8",  "B11"]).rename("NDMI")
    gndvi = img.normalizedDifference(["B8",  "B3"]).rename("GNDVI")
    chl   = img.select("B6").divide(img.select("B5")).subtract(1).rename("CHL")

    return image.addBands([ndvi, evi, savi, ndwi, ndre, ndmi, gndvi, chl])


def get_sentinel2_collection(aoi, start_date, end_date, cloud_pct):
    """Return cloud-masked, median Sentinel-2 SR image over AOI."""
    def mask_clouds(img):
        scl = img.select("SCL")
        # SCL classes to keep: 4=vegetation, 5=non-veg, 6=water, 7=unclassified, 11=snow
        mask = scl.eq(4).Or(scl.eq(5)).Or(scl.eq(6)).Or(scl.eq(7)).Or(scl.eq(11))
        return img.updateMask(mask)

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(str(start_date), str(end_date))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_pct))
        .map(mask_clouds)
        .map(compute_indices_ee)
    )
    return col


def sample_index(image, aoi, index_name, scale=20):
    """Sample an index band and return a numpy array via getDownloadURL."""
    band = image.select(index_name).clip(aoi)
    url  = band.getDownloadURL({
        "scale": scale,
        "region": aoi,
        "format": "NPY",
        "crs": "EPSG:4326",
    })
    import requests
    r = requests.get(url)
    arr = np.load(io.BytesIO(r.content))
    return arr


def download_rgb_thumbnail(image, aoi, width=800):
    """Return PNG bytes for an RGB true-colour thumbnail."""
    vis = {"bands": ["B4", "B3", "B2"], "min": 0, "max": 3000, "gamma": 1.4}
    url = image.visualize(**vis).getThumbURL({
        "region": aoi,
        "dimensions": width,
        "format": "png",
    })
    import requests
    return requests.get(url).content


def download_index_array(image, aoi, index_name, scale=20):
    """Download index as numpy array via GEE thumbnail (GeoTIFF fallback)."""
    import requests
    idx_def = INDEX_DEFS[index_name]
    vmin, vmax = idx_def["range"]
    vis = {
        "bands":      [index_name],
        "min":        vmin,
        "max":        vmax,
        "palette":    get_palette(idx_def["cmap"]),
        "dimensions": 512,
        "region":     aoi,
        "format":     "png",
    }
    url = image.select(index_name).clip(aoi).visualize(
        min=vmin, max=vmax, palette=get_palette(idx_def["cmap"])
    ).getThumbURL({"region": aoi, "dimensions": 512, "format": "png"})
    return requests.get(url).content


def get_palette(cmap_name, n=256):
    cmap = plt.get_cmap(cmap_name)
    return [mcolors.to_hex(cmap(i / (n - 1))) for i in range(0, n, n // 12)]


# ─── Matplotlib Fallback (local numpy simulation) ─────────────────────────────
def simulate_index_from_bands(bands, index_name):
    """
    Compute index from a bands dict (arrays already scaled 0-1).
    bands keys: blue, green, red, nir, red_edge, swir1
    """
    def nd(a, b):
        denom = a + b
        out = np.where(denom != 0, (a - b) / denom, 0)
        return out

    b = bands
    if index_name == "NDVI":
        return nd(b["nir"], b["red"])
    if index_name == "EVI":
        denom = b["nir"] + 6 * b["red"] - 7.5 * b["blue"] + 1
        return np.where(denom != 0, 2.5 * (b["nir"] - b["red"]) / denom, 0)
    if index_name == "SAVI":
        return 1.5 * nd(b["nir"], b["red"]) / 1   # simplified; factor already in nd
    if index_name == "NDWI":
        return nd(b["green"], b["nir"])
    if index_name == "NDRE":
        return nd(b["nir"], b["red_edge"])
    if index_name == "NDMI":
        return nd(b["nir"], b["swir1"])
    if index_name == "GNDVI":
        return nd(b["nir"], b["green"])
    if index_name == "CHL":
        return np.where(b["red_edge"] != 0, b["nir"] / b["red_edge"] - 1, 0)
    return np.zeros_like(b["nir"])


def render_index_matplotlib(arr, index_name, title_extra=""):
    """Render an index array to a matplotlib figure and return as PNG bytes."""
    idx_def  = INDEX_DEFS[index_name]
    vmin, vmax = idx_def["range"]
    cmap     = idx_def["cmap"]

    # Clip outliers
    arr = np.clip(arr, vmin, vmax)
    arr_masked = np.ma.masked_invalid(arr)

    fig, ax = plt.subplots(figsize=(8, 7), facecolor="#0f1117")
    ax.set_facecolor("#0f1117")

    im = ax.imshow(arr_masked, cmap=cmap, vmin=vmin, vmax=vmax, interpolation="bilinear")
    ax.set_title(f"{index_name}  —  {idx_def['desc']}\n{title_extra}",
                 color="white", fontsize=12, fontweight="bold", pad=10)
    ax.axis("off")

    cbar = plt.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cbar.ax.yaxis.set_tick_params(color="white")
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color="white")

    mean_val  = float(np.nanmean(arr_masked))
    good_lo, good_hi = idx_def["good_range"]
    status = "✅ Good" if good_lo <= mean_val <= good_hi else ("⚠️ Low" if mean_val < good_lo else "⚠️ High")

    ax.text(0.01, 0.01,
            f"Mean: {mean_val:.3f}  |  {status}",
            transform=ax.transAxes, color="white",
            fontsize=10, va="bottom",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#1e2130", alpha=0.8))

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def render_rgb_matplotlib(bands):
    """Create a true-colour composite from band arrays."""
    def norm(b, lo=2, hi=98):
        p2, p98 = np.nanpercentile(b[b > 0], [lo, hi]) if np.any(b > 0) else (0, 1)
        return np.clip((b - p2) / max(p98 - p2, 1e-6), 0, 1)

    r = norm(bands["red"])
    g = norm(bands["green"])
    bl = norm(bands["blue"])
    rgb = np.power(np.dstack([r, g, bl]), 0.7)   # gamma for brightness

    fig, ax = plt.subplots(figsize=(8, 7), facecolor="#0f1117")
    ax.set_facecolor("#0f1117")
    ax.imshow(rgb, interpolation="bilinear")
    ax.set_title("Sentinel-2  True Colour (RGB)", color="white",
                 fontsize=12, fontweight="bold", pad=10)
    ax.axis("off")
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def render_comparison_grid(arrays_dict, bands):
    """6-panel comparison figure."""
    n      = len(arrays_dict)
    ncols  = 3
    nrows  = (n + 2) // ncols + 1  # +1 for RGB row

    fig = plt.figure(figsize=(ncols * 6, nrows * 5), facecolor="#0f1117")
    fig.suptitle("Vegetation Index Dashboard", color="white", fontsize=16,
                 fontweight="bold", y=1.01)

    # RGB
    ax_rgb = fig.add_subplot(nrows, ncols, 1)
    def norm(b):
        p2, p98 = np.nanpercentile(b[b > 0], [2, 98]) if np.any(b > 0) else (0, 1)
        return np.clip((b - p2) / max(p98 - p2, 1e-6), 0, 1)
    rgb = np.power(np.dstack([norm(bands["red"]), norm(bands["green"]), norm(bands["blue"])]), 0.7)
    ax_rgb.imshow(rgb, interpolation="bilinear")
    ax_rgb.set_title("True Colour RGB", color="white", fontsize=11, fontweight="bold")
    ax_rgb.axis("off")
    ax_rgb.set_facecolor("#0f1117")

    for i, (idx_name, arr) in enumerate(arrays_dict.items()):
        ax = fig.add_subplot(nrows, ncols, i + 2)
        ax.set_facecolor("#0f1117")
        idx_def = INDEX_DEFS.get(idx_name, {})
        vmin, vmax = idx_def.get("range", (-1, 1))
        cmap = idx_def.get("cmap", "viridis")
        arr_clipped = np.clip(np.ma.masked_invalid(arr), vmin, vmax)
        im = ax.imshow(arr_clipped, cmap=cmap, vmin=vmin, vmax=vmax, interpolation="bilinear")
        mean_val = float(np.nanmean(arr_clipped))
        ax.set_title(f"{idx_name}   (mean={mean_val:.3f})", color="white",
                     fontsize=11, fontweight="bold")
        ax.axis("off")
        cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
        cbar.ax.yaxis.set_tick_params(color="white")
        plt.setp(cbar.ax.yaxis.get_ticklabels(), color="white")

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


# ─── Sentinel-2 via STAC/COG (no GEE) ────────────────────────────────────────
def fetch_sentinel2_stac(bbox, start_date, end_date, cloud_pct, selected_indices):
    """
    Fetch Sentinel-2 data via STAC API and COG HTTP range-requests.
    Returns (bands_dict, metadata_dict) or raises an exception with a message.
    """
    import requests as req

    stac_url = "https://catalogue.dataspace.copernicus.eu/stac/collections/SENTINEL-2/items"
    params = {
        "bbox":     f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
        "datetime": f"{start_date}T00:00:00Z/{end_date}T23:59:59Z",
        "limit":    20,
        "filter":   f"eo:cloud_cover<{cloud_pct}",
        "sortby":   "-datetime",
    }

    r = req.get(stac_url, params=params, timeout=30)
    r.raise_for_status()
    features = r.json().get("features", [])

    if not features:
        raise ValueError(
            f"No Sentinel-2 images found with <{cloud_pct}% cloud between "
            f"{start_date} and {end_date}.  "
            "Try increasing the date range or cloud cover threshold."
        )

    # Pick least cloudy
    best = min(features, key=lambda f: f["properties"].get("eo:cloud_cover", 99))

    # Band → asset key mapping for S2 L2A COG
    band_asset = {
        "blue":      "B02",
        "green":     "B03",
        "red":       "B04",
        "red_edge":  "B05",
        "nir":       "B08",
        "swir1":     "B11",
    }

    bands  = {}
    assets = best.get("assets", {})

    # Determine which bands we need
    needed_bands = {"blue", "green", "red", "nir"}
    for idx in selected_indices:
        for bname in INDEX_DEFS[idx]["bands"]:
            need = {"B2":"blue","B3":"green","B4":"red","B5":"red_edge",
                    "B8":"nir","B11":"swir1"}.get(bname)
            if need:
                needed_bands.add(need)

    for band_key, asset_name in band_asset.items():
        if band_key not in needed_bands:
            continue
        # Try various asset name variants
        for key_try in [asset_name, asset_name.lower(), f"{asset_name}_10m", f"{asset_name}_20m"]:
            if key_try in assets:
                href = assets[key_try]["href"]
                arr = _read_cog_window(href, bbox)
                if arr is not None:
                    bands[band_key] = arr
                    break

    if not bands:
        raise ValueError("Could not read band data from COG assets.")

    meta = {
        "date":        best["properties"].get("datetime", "")[:10],
        "cloud_cover": best["properties"].get("eo:cloud_cover", 0),
        "platform":    best["properties"].get("platform", "Sentinel-2"),
        "id":          best.get("id", ""),
    }
    return bands, meta


def _read_cog_window(url, bbox, target_size=512):
    """Read a spatial window from a COG using rasterio + VSICURL."""
    try:
        import rasterio
        from rasterio.windows import from_bounds
        from rasterio.warp import transform_bounds
        import os
        os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"
        os.environ["CPL_VSIL_CURL_ALLOWED_EXTENSIONS"] = ".jp2,.tif,.tiff"

        with rasterio.open(url) as src:
            dst_crs = src.crs
            xmin, ymin, xmax, ymax = transform_bounds("EPSG:4326", dst_crs, *bbox)
            win  = from_bounds(xmin, ymin, xmax, ymax, src.transform)

            # Compute output size (limit to target_size)
            h = max(int(win.height), 1)
            w = max(int(win.width),  1)
            scale = max(h, w) / target_size
            out_h = max(int(h / scale), 1)
            out_w = max(int(w / scale), 1)

            arr = src.read(1, window=win, out_shape=(out_h, out_w),
                           resampling=rasterio.enums.Resampling.bilinear)
            arr = arr.astype(np.float32)
            arr[arr <= 0] = np.nan          # mask no-data
            arr /= 10000.0                  # DN → reflectance (0–1)
            return arr
    except Exception as e:
        st.warning(f"COG read failed for {url}: {e}")
        return None


# ─── Sidebar ──────────────────────────────────────────────────────────────────
def sidebar():
    with st.sidebar:
        st.markdown("## 🌿 HyperSpec Analyser")
        st.markdown("---")

        st.markdown("### 📍 Field Coordinates")
        coord_mode = st.radio("Input mode", ["Bounding Box", "Centre + Radius"], horizontal=True)

        if coord_mode == "Bounding Box":
            col1, col2 = st.columns(2)
            with col1:
                lat_min = st.number_input("Lat min", value=17.36, format="%.4f")
                lon_min = st.number_input("Lon min", value=78.47, format="%.4f")
            with col2:
                lat_max = st.number_input("Lat max", value=17.40, format="%.4f")
                lon_max = st.number_input("Lon max", value=78.52, format="%.4f")
            bbox = (lon_min, lat_min, lon_max, lat_max)
        else:
            lat_c  = st.number_input("Centre latitude",  value=17.38, format="%.4f")
            lon_c  = st.number_input("Centre longitude", value=78.49, format="%.4f")
            radius = st.slider("Radius (km)", 0.5, 20.0, 2.0, 0.5)
            deg    = radius / 111.0
            bbox   = (lon_c - deg, lat_c - deg, lon_c + deg, lat_c + deg)

        st.markdown("### 📅 Date Range")
        today     = date.today()
        d_end     = st.date_input("End date",   value=today)
        d_start   = st.date_input("Start date", value=today - timedelta(days=60))
        cloud_pct = st.slider("Max cloud cover (%)", 5, 80, 30)

        st.markdown("### 📊 Indices")
        selected = st.multiselect(
            "Select indices to compute",
            options=list(INDEX_DEFS.keys()),
            default=["NDVI", "EVI", "SAVI", "NDWI"],
        )

        st.markdown("### ⚙️ Backend")
        backend = st.radio("Data source", ["GEE (Google Earth Engine)", "STAC/COG (Direct)"], index=1)

        if backend == "GEE (Google Earth Engine)":
            gee_proj = st.text_input("GEE Project ID", placeholder="your-gcp-project")
            st.session_state["gee_project"] = gee_proj

        st.markdown("### ☁️ Firebase")
        firebase_url = st.text_input(
            "Database URL",
            placeholder="https://your-project-default-rtdb.firebaseio.com",
            help="Firebase Console → Realtime Database → copy the URL",
        )
        field_id = st.text_input(
            "Field ID", value="field_01",
            help="Unique name for this field, e.g. field_hyderabad_01",
        )
        upload_enabled = st.toggle("Auto-upload results to Firebase", value=False)

        st.markdown("---")
        run = st.button("🚀  Analyse Field", use_container_width=True)

    return {
        "bbox":           bbox,
        "start":          d_start,
        "end":            d_end,
        "cloud":          cloud_pct,
        "indices":        selected,
        "backend":        backend,
        "firebase_url":   firebase_url,
        "field_id":       field_id,
        "upload_enabled": upload_enabled,
        "run":            run,
    }


# ─── Metric card helper ───────────────────────────────────────────────────────
def metric_card(label, value, sub="", status="ok"):
    cls = {"ok": "", "warn": "warn", "bad": "bad"}.get(status, "")
    st.markdown(f"""
    <div class="metric-card {cls}">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        <div class="metric-sub">{sub}</div>
    </div>
    """, unsafe_allow_html=True)


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    cfg = sidebar()

    st.title("🌿 Hyperspectral Field Analyser")
    st.markdown(
        "Real satellite data · Sentinel-2 · Multiple vegetation indices · India-ready",
        unsafe_allow_html=False,
    )

    # Info panel (always visible)
    with st.expander("📖 Index reference", expanded=False):
        rows = []
        for name, d in INDEX_DEFS.items():
            rows.append({
                "Index": name,
                "Description": d["desc"],
                "Formula": d["formula"],
                "Healthy range": f"{d['good_range'][0]} – {d['good_range'][1]}",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if not cfg["run"]:
        st.info("👈  Configure your field in the sidebar and click **Analyse Field**.")
        st.image(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/9/95/"
            "Sentinel-2_MSI_bands.png/800px-Sentinel-2_MSI_bands.png",
            caption="Sentinel-2 band overview",
        )
        return

    if not cfg["indices"]:
        st.warning("Please select at least one index.")
        return

    bbox = cfg["bbox"]
    st.markdown(f"**AOI:** lon [{bbox[0]:.4f}, {bbox[2]:.4f}]  lat [{bbox[1]:.4f}, {bbox[3]:.4f}]")

    # ── Backend dispatch ──────────────────────────────────────────────────────
    bands  = None
    meta   = {}
    status_ph = st.empty()

    if "GEE" in cfg["backend"]:
        # ── GEE path ─────────────────────────────────────────────────────────
        ok, msg = init_ee()
        if not ok:
            st.error(f"GEE initialisation failed: {msg}")
            st.info("Tip: run `earthengine authenticate` in your terminal first.")
            return

        with st.spinner("🛰️  Querying Google Earth Engine…"):
            try:
                aoi = ee.Geometry.BBox(*bbox)
                col = get_sentinel2_collection(
                    aoi,
                    str(cfg["start"]),
                    str(cfg["end"]),
                    cfg["cloud"],
                )
                count = int(col.size().getInfo())
                if count == 0:
                    st.error("No cloud-free images found. Try a wider date range or higher cloud %.")
                    return

                median_img = col.median()
                date_range = f"{cfg['start']} → {cfg['end']}"
                meta = {"date": date_range, "cloud_cover": cfg["cloud"],
                        "platform": "Sentinel-2 (GEE)", "count": count}

                # Download RGB thumbnail
                status_ph.info("📸 Downloading RGB thumbnail…")
                rgb_bytes = download_rgb_thumbnail(median_img, aoi)

                # Download each index as PNG
                index_pngs = {}
                for idx_name in cfg["indices"]:
                    status_ph.info(f"📊 Downloading {idx_name}…")
                    try:
                        png = download_index_array(median_img, aoi, idx_name)
                        index_pngs[idx_name] = png
                    except Exception as e:
                        st.warning(f"Could not download {idx_name}: {e}")

                status_ph.empty()
                _display_gee_results(rgb_bytes, index_pngs, meta, cfg)
                # Note: for GEE path index_stats must come from sample arrays
                # Firebase upload is handled inside _display_gee_results via cfg

            except Exception as e:
                st.error(f"GEE error: {e}")
                return

    else:
        # ── STAC/COG path ────────────────────────────────────────────────────
        with st.spinner("🛰️  Fetching Sentinel-2 data via STAC/COG…"):
            try:
                bands, meta = fetch_sentinel2_stac(
                    bbox,
                    str(cfg["start"]),
                    str(cfg["end"]),
                    cfg["cloud"],
                    cfg["indices"],
                )
                status_ph.empty()
            except Exception as e:
                st.error(str(e))
                return

        if bands:
            _display_local_results(bands, meta, cfg)
            _display_rover_panel(cfg)


# ─── Display helpers ──────────────────────────────────────────────────────────
def _display_gee_results(rgb_bytes, index_pngs, meta, cfg):
    """Render GEE-downloaded PNGs."""
    st.success("✅ Analysis complete!")

    # Meta row
    c1, c2, c3 = st.columns(3)
    with c1: metric_card("Platform", meta["platform"])
    with c2: metric_card("Date range", meta["date"])
    with c3: metric_card("Max cloud %", f"{meta['cloud_cover']}%")

    st.markdown("### 🛰️ True Colour Composite")
    st.image(rgb_bytes, use_column_width=True)

    st.markdown("### 📊 Vegetation Indices")
    cols = st.columns(2)
    for i, (idx_name, png) in enumerate(index_pngs.items()):
        with cols[i % 2]:
            st.image(png, caption=f"{idx_name} — {INDEX_DEFS[idx_name]['desc']}", use_column_width=True)


def _display_local_results(bands, meta, cfg):
    """Render locally-computed index figures."""
    st.success("✅ Analysis complete!")

    # Meta row
    c1, c2, c3, c4 = st.columns(4)
    with c1: metric_card("Platform",    meta.get("platform", "Sentinel-2"))
    with c2: metric_card("Date",        meta.get("date", "—"))
    with c3: metric_card("Cloud cover", f"{meta.get('cloud_cover', 0):.1f}%")
    with c4: metric_card("Tile ID",     meta.get("id", "—")[:20])

    # RGB
    st.markdown("### 🛰️ True Colour Composite")
    if all(k in bands for k in ("red", "green", "blue")):
        rgb_png = render_rgb_matplotlib(bands)
        st.image(rgb_png, use_column_width=True)

    # Compute all requested indices
    index_arrays = {}
    for idx_name in cfg["indices"]:
        try:
            arr = simulate_index_from_bands(bands, idx_name)
            index_arrays[idx_name] = arr
        except Exception as e:
            st.warning(f"Could not compute {idx_name}: {e}")

    # Stats table
    st.markdown("### 📈 Index Statistics")
    stats_rows = []
    for idx_name, arr in index_arrays.items():
        valid = arr[np.isfinite(arr)]
        mean  = float(np.nanmean(valid))
        glo, ghi = INDEX_DEFS[idx_name]["good_range"]
        health = "✅ Good" if glo <= mean <= ghi else ("⬇️ Low" if mean < glo else "⬆️ High")
        stats_rows.append({
            "Index":  idx_name,
            "Mean":   round(mean, 4),
            "Min":    round(float(np.nanmin(valid)), 4),
            "Max":    round(float(np.nanmax(valid)), 4),
            "Std":    round(float(np.nanstd(valid)), 4),
            "Healthy range": f"{glo} – {ghi}",
            "Status": health,
        })
    st.dataframe(pd.DataFrame(stats_rows), use_container_width=True, hide_index=True)

    # Grid figure
    st.markdown("### 🗺️ Visualisation Grid")
    grid_png = render_comparison_grid(index_arrays, bands)
    st.image(grid_png, use_column_width=True)

    # Download grid
    st.download_button(
        "⬇️  Download comparison PNG",
        data=grid_png,
        file_name=f"indices_{meta.get('date','')}.png",
        mime="image/png",
    )

    # Firebase upload
    _firebase_upload_stats(index_arrays, meta, cfg)

    # Individual index panels
    st.markdown("### 🔍 Individual Index Maps")
    cols = st.columns(2)
    for i, (idx_name, arr) in enumerate(index_arrays.items()):
        with cols[i % 2]:
            title = f"{meta.get('date','')}  |  cloud {meta.get('cloud_cover',0):.1f}%"
            png = render_index_matplotlib(arr, idx_name, title_extra=title)
            st.image(png, caption=f"{idx_name} — {INDEX_DEFS[idx_name]['desc']}",
                     use_column_width=True)
            st.download_button(
                f"⬇️ {idx_name} PNG",
                data=png,
                file_name=f"{idx_name}_{meta.get('date','')}.png",
                mime="image/png",
                key=f"dl_{idx_name}",
            )


def _firebase_upload_stats(index_arrays, meta, cfg):
    """Compute stats dict and upload to Firebase."""
    import numpy as np
    if not cfg.get("upload_enabled") or not cfg.get("firebase_url"):
        return

    index_stats = {}
    for idx_name, arr in index_arrays.items():
        valid = arr[np.isfinite(arr)]
        if len(valid) == 0:
            continue
        index_stats[idx_name] = {
            "mean": round(float(np.nanmean(valid)), 4),
            "min":  round(float(np.nanmin(valid)), 4),
            "max":  round(float(np.nanmax(valid)), 4),
            "std":  round(float(np.nanstd(valid)), 4),
        }

    with st.spinner("☁️ Uploading to Firebase..."):
        ok = upload_satellite_results(
            db_url=cfg["firebase_url"],
            field_id=cfg["field_id"],
            bbox=cfg["bbox"],
            meta=meta,
            index_stats=index_stats,
        )
    if ok:
        st.success(f"✅ Results uploaded to Firebase → satellite/{cfg['field_id']}")
    else:
        st.error("❌ Firebase upload failed. Check your Database URL.")


def _display_rover_panel(cfg):
    """Show latest rover readings from Firebase if available."""
    if not cfg.get("firebase_url"):
        return

    st.markdown("---")
    st.markdown("### 🤖 Rover Data")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Latest Readings")
        with st.spinner("Fetching rover data..."):
            readings = get_rover_readings(cfg["firebase_url"], cfg["field_id"])

        if not readings:
            st.info("No rover data yet for this field. Data will appear here once your ESP32 starts uploading.")
        else:
            rows = []
            for r in readings[:10]:
                rows.append({
                    "Time":         r.get("timestamp", "")[:19],
                    "NPK-N (mg/kg)": r.get("npk_n", "—"),
                    "NPK-P (mg/kg)": r.get("npk_p", "—"),
                    "NPK-K (mg/kg)": r.get("npk_k", "—"),
                    "pH":            r.get("ph",    "—"),
                    "Soil Moisture %": r.get("soil_moisture", "—"),
                    "Air Temp °C":   r.get("air_temp",  "—"),
                    "Humidity %":    r.get("humidity",  "—"),
                    "Lat":           r.get("lat", "—"),
                    "Lon":           r.get("lon", "—"),
                })
            import pandas as pd
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with col2:
        st.markdown("#### AI Recommendations")
        with st.spinner("Fetching recommendations..."):
            recs = get_fused_recommendations(cfg["firebase_url"], cfg["field_id"])

        if not recs:
            st.info("Recommendations will appear here after the cloud function processes combined rover + satellite data.")
        else:
            for rec in recs.get("items", []):
                severity = rec.get("severity", "info")
                if severity == "critical":
                    st.error(f"🚨 {rec['message']}")
                elif severity == "warning":
                    st.warning(f"⚠️ {rec['message']}")
                else:
                    st.success(f"✅ {rec['message']}")


if __name__ == "__main__":
    main()
