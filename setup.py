import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="NovelGridWorlds",
    version="2.0.0",
    author="Tufts University",
    author_email="Shivam.Goel@tufts.edu",
    description="NovelGridWorlds V2",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/waymao/NovelGridWorldsV2",
    project_urls={
        "Bug Tracker": "https://github.com/waymao/NovelGridWorldsV2/issues",
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
    package_dir={"": "src"},
    packages=setuptools.find_packages(where="src"),
    python_requires=">=3.6",
)
