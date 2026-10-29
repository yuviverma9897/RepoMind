from app.services import file_filter as ff


def test_paths_are_filtered_with_reasons():
    assert ff.check_path("src/app.py", 100) is None
    assert ff.check_path("web/src/App.tsx", 100) is None
    assert ff.check_path("node_modules/react/index.js", 100) == ff.IGNORED_DIRECTORY
    assert ff.check_path("a/dist/bundle.js", 100) == ff.IGNORED_DIRECTORY
    assert ff.check_path("yarn.lock", 100) == ff.GENERATED
    assert ff.check_path("package-lock.json", 100) == ff.GENERATED
    assert ff.check_path("static/app.min.js", 100) == ff.GENERATED
    assert ff.check_path("logo.png", 100) == ff.BINARY
    assert ff.check_path("config.yml", 100) == ff.UNSUPPORTED
    assert ff.check_path("Makefile", 100) == ff.UNSUPPORTED
    assert ff.check_path("data/huge.json", 10_000_000) == ff.TOO_LARGE


def test_build_is_only_ignored_as_a_directory():
    assert ff.check_path("build.py", 100) is None
    assert ff.check_path("src/build/index.js", 100) == ff.IGNORED_DIRECTORY


def test_decode_source():
    assert ff.decode_source(b"print(1)\r\nprint(2)\r\n") == ("print(1)\nprint(2)\n", None)
    assert ff.decode_source("\ufeffx = 1".encode("utf-8")) == ("x = 1", None)
    assert ff.decode_source(b"abc\x00def") == (None, ff.BINARY)
    assert ff.decode_source("café".encode("latin-1")) == (None, ff.BINARY)
    assert ff.decode_source(b"  \n\n ") == (None, ff.EMPTY)
