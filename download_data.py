"""
download_data.py

Downloads all datasets needed for the Grid Congestion and Curtailment project.
Run this script once before opening the notebook.

Sources used:
  - EIA-930 Hourly Electric Grid Monitor (eia.gov) -- national hourly demand/generation/interchange
  - EPA eGRID 2023 (epa.gov) -- emissions intensity and generation mix by grid subregion
  - HIFLD Transmission Lines (ArcGIS REST API, public, no auth) -- US transmission network shapefile
  - LBNL Queued Up (emp.lbl.gov) -- US interconnection queue data across all ISOs

All sources are official US government or DOE national lab publications.
No scraping. No paid APIs. No authentication required (LBNL requires manual download -- see note below).
"""

import os
import time
import requests

# ----- configuration -----

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

EIA930_DIR  = os.path.join(DATA_DIR, "eia930")
EGRID_DIR   = os.path.join(DATA_DIR, "egrid")
HIFLD_DIR   = os.path.join(DATA_DIR, "hifld")
LBNL_DIR    = os.path.join(DATA_DIR, "lbnl_queue")

# EIA-930 six-month bulk CSV files.
# Pattern confirmed from EIA's own file server at:
#   https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/
# Each file covers one balancing-authority-level six-month window.
# We pull 2022-2024 (3 full years) to give enough range for trend analysis
# and to include the 2022-2023 period of record renewable curtailment growth.
EIA930_FILES = [
    "EIA930_BALANCE_2022_Jan_Jun.csv",
    "EIA930_BALANCE_2022_Jul_Dec.csv",
    "EIA930_BALANCE_2023_Jan_Jun.csv",
    "EIA930_BALANCE_2023_Jul_Dec.csv",
    "EIA930_BALANCE_2024_Jan_Jun.csv",
    "EIA930_BALANCE_2024_Jul_Dec.csv",
]
EIA930_BASE_URL = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/"

# EPA eGRID 2023 data file (Revision 2, released June 2025).
# eGRID is the authoritative source for emissions intensity and generation mix
# by grid subregion -- we use this to characterize each region's fuel mix,
# which becomes a control variable in the cross-region statistical comparison.
EGRID_URL  = "https://www.epa.gov/system/files/documents/2025-06/egrid2023_data_rev2.xlsx"
EGRID_FILE = "egrid2023_data_rev2.xlsx"

# HIFLD Transmission Lines via the ArcGIS REST API (public, no auth, HTTPS).
# This endpoint returns the full US high-voltage transmission network as GeoJSON.
# We use recordCount=2000 with offset pagination because the API caps at 2000
# features per request. The full dataset has ~75,000 line features.
# The resultRecordCount param controls page size; resultOffset steps through pages.
# f=geojson returns GeoJSON directly -- readable by geopandas with no conversion.
HIFLD_REST_URL = (
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/"
    "Electric_Power_Transmission_Lines/FeatureServer/0/query"
)
HIFLD_FILE = "transmission_lines.geojson"


def download_file(url, dest_path, label):
    """
    Download a single file from a URL to a local path.

    Uses streaming so large files do not load entirely into memory before
    being written to disk. Prints progress in 10MB chunks.

    Skips if the file already exists -- safe to re-run without re-downloading.
    """
    if os.path.exists(dest_path):
        print(f"  already exists, skipping: {os.path.basename(dest_path)}")
        return True

    print(f"  downloading {label} ...")
    try:
        response = requests.get(url, stream=True, timeout=120)
        response.raise_for_status()

        total = 0
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=10 * 1024 * 1024):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
                    print(f"    {total / 1e6:.1f} MB downloaded", end="\r")
        print(f"    done -- {total / 1e6:.1f} MB saved to {os.path.basename(dest_path)}")
        return True

    except requests.HTTPError as e:
        print(f"  ERROR: HTTP {e.response.status_code} for {url}")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def download_hifld_transmission_lines(dest_path):
    """
    Download the HIFLD transmission lines dataset via the ArcGIS Feature Service REST API.

    Why paginate: the API caps responses at 2000 features per call, but the full dataset
    has ~75,000 line segments. We loop with a resultOffset increment of 2000 until we
    get fewer than 2000 features back, which signals the last page.

    Why GeoJSON: geopandas reads GeoJSON natively with geopandas.read_file(), no
    conversion step needed. The combined output is written as one GeoJSON FeatureCollection.

    This is the same data available through the HIFLD ArcGIS Hub "Download" button --
    we are just automating that click via the public REST endpoint.
    """
    if os.path.exists(dest_path):
        print(f"  already exists, skipping: {os.path.basename(dest_path)}")
        return True

    print("  downloading HIFLD transmission lines (paginated) ...")

    all_features = []
    offset = 0
    page_size = 2000
    page_num = 1

    while True:
        params = {
            "where": "1=1",
            "outFields": "OBJECTID,TYPE,VOLTAGE,SUB_1,SUB_2,SHAPE__Length",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        }

        try:
            response = requests.get(HIFLD_REST_URL, params=params, timeout=60)
            response.raise_for_status()
            page_data = response.json()
        except Exception as e:
            print(f"  ERROR on page {page_num}: {e}")
            return False

        features = page_data.get("features", [])
        all_features.extend(features)
        print(f"    page {page_num}: {len(features)} features (total so far: {len(all_features)})", end="\r")

        if len(features) < page_size:
            # last page -- fewer records than the page cap means we hit the end
            break

        offset += page_size
        page_num += 1
        time.sleep(0.5)  # polite delay so we do not hammer the API

    print(f"\n    total features collected: {len(all_features)}")

    # assemble into a single GeoJSON FeatureCollection and write to disk
    geojson_out = {
        "type": "FeatureCollection",
        "features": all_features,
    }

    import json
    with open(dest_path, "w") as f:
        json.dump(geojson_out, f)

    size_mb = os.path.getsize(dest_path) / 1e6
    print(f"    saved {size_mb:.1f} MB to {os.path.basename(dest_path)}")
    return True


def main():
    print("=" * 60)
    print("Grid Congestion Project -- Data Downloader")
    print("=" * 60)

    # --- EIA-930 ---
    print("\n[1/3] EIA-930 Hourly Grid Monitor (2022-2024)")
    for filename in EIA930_FILES:
        url = EIA930_BASE_URL + filename
        dest = os.path.join(EIA930_DIR, filename)
        download_file(url, dest, filename)

    # --- eGRID ---
    print("\n[2/3] EPA eGRID 2023")
    dest = os.path.join(EGRID_DIR, EGRID_FILE)
    download_file(EGRID_URL, dest, EGRID_FILE)

    # --- HIFLD ---
    print("\n[3/3] HIFLD Transmission Lines (US)")
    dest = os.path.join(HIFLD_DIR, HIFLD_FILE)
    download_hifld_transmission_lines(dest)

    # --- LBNL note ---
    print("\n[Note] LBNL Queued Up interconnection queue data")
    print("  This file cannot be downloaded automatically (the server blocks bots).")
    print("  Please visit: https://emp.lbl.gov/queues")
    print("  Download the Excel file manually and place it in:")
    print(f"  {LBNL_DIR}/")
    print("  Rename it to: queued_up_2025.xlsx")

    print("\nDone. All automatic downloads complete.")
    print(f"Data folder: {DATA_DIR}")


if __name__ == "__main__":
    main()
