from setuptools import setup, find_packages
from glob import glob

setup(
    name="pac_planning",
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        (
            "share/ament_index/resource_index/packages",
            ["resource/pac_planning"],
        ),
        ("share/pac_planning", ["package.xml"]),
        ("share/pac_planning/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    entry_points={"console_scripts": [
        "placement_planner_node = pac_planning.planning_service:main",
    ]},
    zip_safe=True,
    maintainer="AHEAD team",
    maintainer_email="maintainers@example.invalid",
    description=(
        "PAC 2026 common contracts"
        if "pac_planning" == "pac_common"
        else "AHEAD planning stages 5-3 to 5-6"
    ),
    license="LicenseRef-All-Rights-Reserved",
)
