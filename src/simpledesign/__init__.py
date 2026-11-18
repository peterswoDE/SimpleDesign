"""SimpleDesign - a simple parametric CAD tool for wooden furniture."""

__version__ = "0.1.0"


def main() -> int:
    """Entry point of the desktop application."""
    # Imported lazily so that `import simpledesign` stays cheap (e.g. for the core in tests).
    from simpledesign.ui.app import run

    return run()
