"""
NOTE: This script assumes you have already downloaded and extracted the Places365 validation images and metadata files.
If you haven't done that yet, you can run the following commands in your terminal:
wget http://data.csail.mit.edu/places/places365/val_256.tar
tar -xf val_256.tar
wget https://data.csail.mit.edu/places/places365/filelist_places365-standard.tar
tar -xf filelist_places365-standard.tar
"""

from pathlib import Path
import shutil

# =========================
# Configuration
# =========================

# Source validation image directory
SOURCE_DIR = Path("data/places365/val_256")

# Output directory for selected images
OUTPUT_DIR = Path("places365_selected")

# Official Places365 metadata files
CATEGORIES_FILE = Path("categories_places365.txt")
VAL_LABEL_FILE = Path("places365_val.txt")

# Target categories using official Places365 names
TARGET_CATEGORIES = {
    "bridge",
    "viaduct",
    "river",
    "dam",
    "highway",
    "harbor",
    "rope_bridge",
    "construction_site",
    "canal/natural",
    "canal/urban",
    "lake/natural",
}

# "move" relocates files; "copy" keeps originals
MODE = "move"

# True: create per-category subfolders
# False: place everything directly under OUTPUT_DIR
MAKE_CATEGORY_SUBFOLDERS = True

# Allowed input suffixes
VALID_SUFFIXES = {".jpg", ".jpeg", ".png"}


# =========================
# Helpers
# =========================

def load_id_to_category(categories_file: Path):
    """
    Parse `categories_places365.txt`.

    Each line looks like:
    /a/abbey 0
    /b/bridge 66
    """
    id_to_cat = {}
    with categories_file.open("r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            cat = parts[0][3:]   # Strip the leading `/x/` prefix.
            idx = int(parts[1])
            id_to_cat[idx] = cat
    return id_to_cat


def load_val_filename_to_label(val_label_file: Path):
    """
    Parse `places365_val.txt`.

    Each line looks like:
    Places365_val_00000001.jpg 123
    """
    val_to_label = {}
    with val_label_file.open("r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) != 2:
                continue
            fname, label = parts
            val_to_label[fname] = int(label)
    return val_to_label


def local_to_official_name(local_filename: str):
    """
    Supported inputs:
    1. Places365_val_00001234.jpg
    2. 00001234.jpg

    Returns the official filename format:
    Places365_val_00001234.jpg
    """
    p = Path(local_filename)
    stem = p.stem
    suffix = p.suffix.lower()

    if stem.startswith("Places365_val_"):
        return p.name

    # Only pure numeric indices are accepted.
    # Example: 00001234 -> Places365_val_00001234.jpg
    if stem.isdigit():
        return f"Places365_val_{stem}{suffix}"

    return None


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


# =========================
# Main
# =========================

def main():
    if MODE not in {"move", "copy"}:
        raise ValueError("MODE must be either 'move' or 'copy'")

    if not SOURCE_DIR.exists():
        raise FileNotFoundError(f"SOURCE_DIR does not exist: {SOURCE_DIR}")

    if not CATEGORIES_FILE.exists():
        raise FileNotFoundError(f"Cannot find {CATEGORIES_FILE}")

    if not VAL_LABEL_FILE.exists():
        raise FileNotFoundError(f"Cannot find {VAL_LABEL_FILE}")

    ensure_dir(OUTPUT_DIR)

    id_to_cat = load_id_to_category(CATEGORIES_FILE)
    val_to_label = load_val_filename_to_label(VAL_LABEL_FILE)

    # Validate requested categories against the metadata table.
    available_categories = set(id_to_cat.values())
    missing_categories = sorted(TARGET_CATEGORIES - available_categories)
    if missing_categories:
        print("These categories are not present in the Places365 category list:")
        for c in missing_categories:
            print("  ", c)
        print()

    matched_count = 0
    skipped_count = 0
    unknown_name_count = 0

    for img_path in sorted(SOURCE_DIR.iterdir()):
        if not img_path.is_file():
            continue
        if img_path.suffix.lower() not in VALID_SUFFIXES:
            continue

        official_name = local_to_official_name(img_path.name)
        if official_name is None:
            unknown_name_count += 1
            print(f"[skip] Unrecognized filename format: {img_path.name}")
            continue

        if official_name not in val_to_label:
            skipped_count += 1
            print(f"[skip] No match found in places365_val.txt: {official_name}")
            continue

        label_id = val_to_label[official_name]
        category = id_to_cat[label_id]

        if category not in TARGET_CATEGORIES:
            continue

        if MAKE_CATEGORY_SUBFOLDERS:
            dst_dir = OUTPUT_DIR / category.replace("/", "__")
        else:
            dst_dir = OUTPUT_DIR

        ensure_dir(dst_dir)
        dst_path = dst_dir / img_path.name

        # Avoid overwriting existing files.
        if dst_path.exists():
            stem = dst_path.stem
            suffix = dst_path.suffix
            i = 1
            while True:
                alt = dst_dir / f"{stem}_{i}{suffix}"
                if not alt.exists():
                    dst_path = alt
                    break
                i += 1

        if MODE == "move":
            shutil.move(str(img_path), str(dst_path))
        else:
            shutil.copy2(str(img_path), str(dst_path))

        matched_count += 1
        print(f"[{MODE}] {img_path.name} -> {dst_path}  ({category})")

    print("\n===== Done =====")
    print(f"Matched and processed images: {matched_count}")
    print(f"Skipped due to missing label mapping: {skipped_count}")
    print(f"Skipped due to unrecognized filename format: {unknown_name_count}")


if __name__ == "__main__":
    main()
