from protocli import Dispatcher


def main() -> None:
    Dispatcher(
        "ramore",
        {"report": "ramore.report.run", "reset": "ramore.report.reset"},
    ).run()


if __name__ == "__main__":
    main()
