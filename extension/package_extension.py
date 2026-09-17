import zipfile
from pathlib import Path

def package():
    ext_dir = Path("extension")
    out_zip = Path("autoapply-extension.zip")

    if out_zip.exists():
        out_zip.unlink()

    with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED) as z:
        for file in ext_dir.rglob('*'):
            if file.is_file() and not file.name.endswith('.py'):
                arcname = file.relative_to(ext_dir)
                z.write(file, arcname)
                print(f"Added: {arcname}")

    print(f"\nSuccessfully packaged extension -> {out_zip.resolve()} ({out_zip.stat().st_size} bytes)")

if __name__ == "__main__":
    package()
