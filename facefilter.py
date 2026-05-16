from pathlib import Path
import cv2 as cv

FOLDER = Path(r"C:\Users\qacer\Downloads\py\fb_photos")

# Put this file in the same folder as the script:
# face_detection_yunet_2023mar.onnx
SCRIPT_DIR = Path(__file__).resolve().parent
DETECTOR_MODEL = str(SCRIPT_DIR / "face_detection_yunet_2023mar.onnx")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def strip_prefix(name: str) -> str:
    for prefix in ("keep_", "to_be_deleted_"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def build_new_name(path: Path, prefix: str) -> Path:
    return path.with_name(prefix + strip_prefix(path.name))


def safe_rename(src: Path, dst: Path):
    if src.resolve() == dst.resolve():
        return

    if dst.exists():
        print(f"SKIP target exists: {dst.name}")
        return

    src.rename(dst)


def load_image(path: Path):
    img = cv.imread(str(path))
    if img is None:
        raise ValueError(f"Could not read image: {path}")
    return img


def create_detector():
    detector = cv.FaceDetectorYN.create(
        DETECTOR_MODEL,
        "",
        (320, 320),
        score_threshold=0.9,
        nms_threshold=0.3,
        top_k=5000,
    )
    return detector


def detect_faces(detector, image):
    h, w = image.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(image)
    if faces is None:
        return []
    return faces


def main():
    if not FOLDER.exists():
        raise FileNotFoundError(f"Folder not found: {FOLDER}")

    if not Path(DETECTOR_MODEL).exists():
        raise FileNotFoundError(f"Missing model file: {DETECTOR_MODEL}")

    detector = create_detector()

    total = 0
    kept = 0
    deleted = 0
    errors = 0

    for file_path in FOLDER.iterdir():
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        total += 1

        try:
            img = load_image(file_path)
            faces = detect_faces(detector, img)

            if len(faces) > 0:
                new_path = build_new_name(file_path, "keep_")
                safe_rename(file_path, new_path)
                print(f"KEEP ({len(faces)} face(s)) -> {new_path.name}")
                kept += 1
            else:
                new_path = build_new_name(file_path, "to_be_deleted_")
                safe_rename(file_path, new_path)
                print(f"DELETE (no face) -> {new_path.name}")
                deleted += 1

        except Exception as e:
            print(f"ERROR {file_path.name}: {e}")
            errors += 1

    print("\nDone.")
    print(f"Processed: {total}")
    print(f"Keep: {kept}")
    print(f"To be deleted: {deleted}")
    print(f"Errors: {errors}")


if __name__ == "__main__":
    main()