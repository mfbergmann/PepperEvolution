#!/usr/bin/env python3
"""
Setup script for PepperEvolution
"""

from setuptools import setup, find_packages
import os

# Read the README file
def read_readme():
    with open("README.md", "r", encoding="utf-8") as fh:
        return fh.read()

# Read requirements
def read_requirements():
    with open("requirements.txt", "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="pepper-evolution",
    version="0.1.0",
    author="PepperEvolution Team",
    author_email="contact@pepperevolution.org",
    description="Cloud-based AI control system for Pepper robots",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/YOUR_USERNAME/PepperEvolution",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.8",
    install_requires=read_requirements(),
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "pytest-mock>=3.11.0",
            "black>=23.0.0",
            "flake8>=6.0.0",
            "mypy>=1.5.0",
            "pre-commit>=3.5.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "pepper-evolution=main:main",
        ],
    },
    include_package_data=True,
    zip_safe=False,
    keywords="pepper robot ai naoqi cloud robotics",
    project_urls={
        "Bug Reports": "https://github.com/YOUR_USERNAME/PepperEvolution/issues",
        "Source": "https://github.com/YOUR_USERNAME/PepperEvolution",
        "Documentation": "https://github.com/YOUR_USERNAME/PepperEvolution/docs",
    },
)
