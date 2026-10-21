from protocli import Dispatcher


def main() -> None:
    Dispatcher(
        "ramore",
        {
            "report": "ramore.report.run",
            "reset": "ramore.report.reset",
            "box": "ramore.box",
            "demo": "ramore.demo",
            "monitor": "ramore.monitor",
            "units": "ramore.units",
        },
    ).run()


if __name__ == "__main__":
    main()
