"""
download.py - Download BTS flight data for the Flight Delay Analytics Pipeline.

What this script does, in order:
  1. For every month in the range set below, download the BTS
     "Reporting Carrier On-Time Performance (1987-present)" zip file and
     pull the one CSV file out of it into data/raw/.
  2. Download two BTS lookup tables that turn codes into names:
       - L_UNIQUE_CARRIERS: airline code -> airline name   (e.g. "DL" -> "Delta Air Lines Inc.")
       - L_AIRPORT:         airport code -> description    (e.g. "JFK" -> "New York, NY: John F. Kennedy International")

Caching: if a file is already in data/raw/, it is skipped, so running the
script again only downloads what is missing.

Run it from a terminal with:   python download.py
"""

# --- Imports -----------------------------------------------------------------
# All of these come with Python itself, so nothing extra needs installing.
import shutil          # copies a file from inside the zip to disk in chunks
import urllib.error    # the error types urllib raises (e.g. "404 Not Found")
import urllib.request  # downloads a file from a URL
import zipfile         # opens .zip files
from pathlib import Path  # handles file paths so they work on Windows and Mac/Linux

# --- Settings you can change --------------------------------------------------
# The month range to download. Both ends are included.
# Aug 2025 through Jul 2026 = 12 months.
START_YEAR, START_MONTH = 2025, 8
END_YEAR, END_MONTH = 2026, 7

# --- Fixed settings -------------------------------------------------------------
# Path(__file__).parent is the folder this script is saved in (the repo root).
# Building paths from it means the script works no matter which folder you
# run it from.
REPO_ROOT = Path(__file__).parent
RAW_DIR = REPO_ROOT / "data" / "raw"

# BTS keeps one pre-made zip per month at this address. {year} and {month} are
# filled in later. The month has NO leading zero (2025_8, not 2025_08).
# I checked this URL by hand: Aug 2025 returns the zip, and a month that is not
# published yet returns "404 Not Found".
MONTH_URL = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)

# The name of the CSV inside each zip. Note the brackets around 1987_present:
# the zip's name does not have them, but the CSV inside does. This is the same
# naming as the July 2026 file used in Phase 1.
CSV_NAME = "On_Time_Reporting_Carrier_On_Time_Performance_(1987_present)_{year}_{month}.csv"

# The lookup tables. BTS hides the table name in the URL with a simple letter
# scramble (ROT13: every letter is shifted 13 places, so "L" becomes "Y",
# "UNIQUE" becomes "HAVDHR", and so on). The mixed upper/lower case matters:
# with the wrong case BTS returns an error page. I copied these two links
# straight from the "Get Lookup Table" links on the BTS download page:
# https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FGJ&QO_fu146_anzr=b0-gvzr
LOOKUP_BASE = "https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72="
LOOKUPS = {
    # file name we save as      : scrambled table name in the URL
    "L_UNIQUE_CARRIERS.csv": "Y_haVdhR_PNeeVRef",
    "L_AIRPORT.csv": "Y_NVecbeg",
}

# Some servers refuse requests that do not say what program is asking.
# This header just says "a Python script".
HEADERS = {"User-Agent": "Mozilla/5.0 (flight-delay-pipeline download script)"}


def month_range(start_year, start_month, end_year, end_month):
    """Return a list of (year, month) pairs from the start month to the end month, inclusive.

    Example: month_range(2025, 11, 2026, 2)
             -> [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]
    """
    months = []
    year, month = start_year, start_month
    # Tuples compare item by item: (2025, 12) <= (2026, 7) because 2025 < 2026.
    while (year, month) <= (end_year, end_month):
        months.append((year, month))
        month += 1
        if month == 13:  # roll over from December to January of the next year
            month = 1
            year += 1
    return months


def download_file(url, destination):
    """Download `url` and save it to `destination`.

    The file is first written as "<name>.part" and only renamed to its real
    name once the download has fully finished. If the download stops halfway
    (lost connection, Ctrl+C), only the .part file is left behind, so a broken
    half-file is never mistaken for a finished one by the caching check.
    """
    temp_path = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(url, headers=HEADERS)
    # "with" closes the connection and the file automatically when done.
    with urllib.request.urlopen(request, timeout=120) as response, open(temp_path, "wb") as out_file:
        # Copy in 1 MB chunks so a 300 MB file never has to fit in memory at once.
        shutil.copyfileobj(response, out_file, length=1024 * 1024)
    temp_path.replace(destination)  # rename .part -> real name


def download_month(year, month):
    """Download and extract one month. Returns True if the CSV is ready, False if not."""
    label = f"{year}-{month:02d}"  # e.g. "2025-08", only used for printing
    csv_path = RAW_DIR / CSV_NAME.format(year=year, month=month)
    zip_path = RAW_DIR / f"{year}_{month}.zip"

    # Caching: the CSV is already there, so there is nothing to do.
    if csv_path.exists():
        print(f"[skip] {label}: already in data/raw/")
        return True

    # Step 1: download the zip.
    url = MONTH_URL.format(year=year, month=month)
    print(f"[download] {label}: {url}")
    try:
        download_file(url, zip_path)
    except urllib.error.HTTPError as error:
        # HTTPError means the server answered, but with an error code.
        if error.code == 404:
            # 404 = "Not Found". For BTS this almost always means the month
            # has not been published yet (BTS releases data about 2 months late).
            print(f"[ERROR] {label}: not available on BTS yet (404 Not Found). Skipping this month.")
        else:
            print(f"[ERROR] {label}: BTS returned HTTP {error.code} ({error.reason}). Skipping this month.")
        return False
    except urllib.error.URLError as error:
        # URLError means we never got an answer: no internet, DNS problem, etc.
        print(f"[ERROR] {label}: could not reach BTS ({error.reason}). Check your internet connection.")
        return False

    # Step 2: make sure what we got really is a zip file (not an error web page).
    if not zipfile.is_zipfile(zip_path):
        print(f"[ERROR] {label}: the downloaded file is not a valid zip. Skipping this month.")
        zip_path.unlink()  # delete the bad file so the next run tries again
        return False

    # Step 3: pull the CSV out of the zip. The zip also contains a readme.html,
    # which we ignore. We look for the one file ending in ".csv".
    with zipfile.ZipFile(zip_path) as zf:
        csv_members = [name for name in zf.namelist() if name.lower().endswith(".csv")]
        if len(csv_members) != 1:
            print(f"[ERROR] {label}: expected 1 CSV in the zip, found {len(csv_members)}. Skipping this month.")
            return False
        # Same ".part then rename" trick as in download_file(), and the same
        # chunked copy so the whole CSV is never held in memory.
        temp_csv = csv_path.with_name(csv_path.name + ".part")
        with zf.open(csv_members[0]) as src, open(temp_csv, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        temp_csv.replace(csv_path)

    # Step 4: delete the zip. The CSV holds everything we need, and keeping
    # both would store each month twice. (Caching checks for the CSV, not the zip.)
    zip_path.unlink()
    size_mb = csv_path.stat().st_size / 1_000_000
    print(f"[done] {label}: saved {csv_path.name} ({size_mb:.0f} MB)")
    return True


def download_lookup(file_name, scrambled_name):
    """Download one lookup table and save it to data/raw/ as UTF-8 text."""
    path = RAW_DIR / file_name
    if path.exists():
        print(f"[skip] {file_name}: already in data/raw/")
        return True

    url = LOOKUP_BASE + scrambled_name
    print(f"[download] {file_name}: {url}")
    try:
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=120) as response:
            raw_bytes = response.read()  # these files are small (under 1 MB)
    except urllib.error.URLError as error:  # HTTPError is a kind of URLError, so this catches both
        print(f"[ERROR] {file_name}: download failed ({error}).")
        return False

    # BTS saves these files in the old Windows-1252 text encoding, not UTF-8.
    # For example L_AIRPORT has a name with an accented "I" stored as a single
    # byte that is invalid in UTF-8, and DuckDB refuses to read it. Decoding as
    # Windows-1252 and saving as UTF-8 fixes that once, here, so the notebook
    # can read the file without any special settings.
    text = raw_bytes.decode("cp1252")

    # Check we got the table and not an error web page: the first line should be the header.
    if not text.startswith("Code,Description"):
        print(f"[ERROR] {file_name}: response does not look like a lookup table. Not saved.")
        return False

    # newline="" writes the line endings exactly as BTS sent them.
    path.write_text(text, encoding="utf-8", newline="")
    rows = text.count("\n") - 1  # minus 1 for the header line
    print(f"[done] {file_name}: saved {rows:,} rows")
    return True


def main():
    # Create data/raw/ (and data/ if needed). exist_ok=True means "fine if it's already there".
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    months = month_range(START_YEAR, START_MONTH, END_YEAR, END_MONTH)
    print(f"Months requested: {len(months)} "
          f"({START_YEAR}-{START_MONTH:02d} to {END_YEAR}-{END_MONTH:02d})\n")

    # Keep a list of any month that failed so we can report it at the end.
    failed = []
    for i, (year, month) in enumerate(months, start=1):
        print(f"({i}/{len(months)}) ", end="")  # progress counter, e.g. "(3/12) "
        if not download_month(year, month):
            failed.append(f"{year}-{month:02d}")

    print("\nLookup tables:")
    for file_name, scrambled_name in LOOKUPS.items():
        if not download_lookup(file_name, scrambled_name):
            failed.append(file_name)

    # Final summary.
    print()
    if failed:
        print(f"Finished with problems. Not downloaded: {', '.join(failed)}")
    else:
        print("Finished. All months and lookup tables are in data/raw/.")


# This block only runs when you start the file directly (python download.py),
# not when another file imports it.
if __name__ == "__main__":
    main()
