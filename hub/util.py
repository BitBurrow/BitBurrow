import os
import importlib.metadata


class Berror(Exception):
    """Raise for probably-fatal errors (calling method can decide). Include a Berror code
    (https://bitburrow.com/hub/#berror-codes) and any potentially useful details.
    """

    pass


def rotate_backups(file_path: str, prefile_path: str, max_versions: int = 9):
    """Rotate file backups e.g. name.1.txt → name.2.txt and then name.txt → name.1.txt (via
    hard link for atomic replacement) and finally pre.txt → name.txt. The file_path and
    prefile_path must both exist and be in the same directory."""
    assert os.path.dirname(file_path) == os.path.dirname(prefile_path)
    assert max_versions >= 1
    base, ext = os.path.splitext(file_path)
    for v in range(max_versions, 0, -1):
        dst = f'{base}.{v}{ext}'
        if v > 1:
            src = f'{base}.{v-1}{ext}'
            try:
                os.replace(src, dst)  # mv name.8.txt name.9.txt
            except FileNotFoundError:
                pass
        else:  # last pair: carefully move prefile into its new place
            try:
                os.remove(dst)  # should be gone, but let's be sure
            except FileNotFoundError:
                pass
            os.link(file_path, dst)  # ln name.txt name.1.txt  # hard link so migration is atomic
            os.replace(prefile_path, file_path)  # mv name-EBBWIL.txt name.txt


def app_version() -> str:
    try:
        return importlib.metadata.version("bitburrow")
    except importlib.metadata.PackageNotFoundError:
        return '(unknown)'
