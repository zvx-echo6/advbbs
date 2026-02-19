"""
advBBS Setup Script
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read() if __file__ else ""

setup(
    name="advbbs",
    version="0.1.0",
    author="advBBS Project",
    description="Lightweight BBS for Meshtastic Mesh Networks",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/zvx-echo6/advbbs",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Communications :: BBS",
    ],
    python_requires=">=3.9",
    install_requires=[
        "meshtastic>=2.3.0",
        "pypubsub>=4.0.3",
        "argon2-cffi>=23.1.0",
        "cryptography>=41.0.0",
        "tomli>=2.0.0;python_version<'3.11'",
        "toml>=0.10.2",
    ],
    extras_require={
        "web": ["flask>=3.0.0"],
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.21.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "advbbs=advbbs.__main__:main",
        ],
    },
)
