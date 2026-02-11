#!/usr/bin/env python3
"""
Setup script for PepperEvolution
"""

from setuptools import setup, find_packages


def read_readme():
    with open("README.md", "r", encoding="utf-8") as fh:
        return fh.read()


def read_requirements():
    with open("requirements.txt", "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]


setup(
    name="pepper-evolution",
    version="2.0.0",
    author="PepperEvolution Team",
    author_email="mfb@torontomu.ca",
    description="Cloud-based AI control system for Pepper robots",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/mfbergmann/PepperEvolution",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.12",
    install_requires=read_requirements(),
    extras_require={
        "dev": [
            "pytest>=8.3.0",
            "pytest-asyncio>=0.24.0",
            "respx>=0.22.0",
            "black>=24.0.0",
            "flake8>=7.0.0",
            "mypy>=1.13.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "pepper-evolution=main:main",
        ],
    },
    include_package_data=True,
    zip_safe=False,
    keywords="pepper robot ai naoqi cloud robotics anthropic",
    project_urls={
        "Bug Reports": "https://github.com/mfbergmann/PepperEvolution/issues",
        "Source": "https://github.com/mfbergmann/PepperEvolution",
        "Documentation": "https://github.com/mfbergmann/PepperEvolution/docs",
    },
)
