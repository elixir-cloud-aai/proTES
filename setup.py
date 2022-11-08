"""Package setup."""

from pathlib import Path
from setuptools import (setup, find_packages)

root_dir = Path(__file__).parent.resolve()

exec(open(root_dir / "pro_tes" / "version.py").read())

file_name = root_dir / "README.md"
with open(file_name, 'r') as _file:
    long_description = _file.read()

install_requires = []
req = root_dir / 'requirements.txt'
with open(req, "r") as _file:
    install_requires = _file.read().splitlines()

setup(
    name='pro-tes',
    license='Apache License 2.0',
    version=__version__,  # noqa: F821
    description='Proxy/gateway GA4GH TES service',
    long_description=long_description,
    long_description_content_type="text/markdown",
    url='https://github.com/elixir-cloud-aai/proTES',
    author='Alexander Kanitz',
    author_email='alexander.kanitz@alumni.ethz.ch',
    maintainer='ELIXIR Cloud & AAI',
    maintainer_email='cloud-service@elixir-europe.org',
    classifiers=[
        'License :: OSI Approved :: Apache Software License',
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Science/Research',
        'Topic :: Scientific/Engineering :: Bio-Informatics',
        'Natural Language :: English',
        'Programming Language :: Python :: 3.6',
    ],
    keywords=(
        'ga4gh tes proxy rest restful api app server openapi '
        'swagger python flask'
    ),
    project_urls={
        "Repository": "https://github.com/elixir-cloud-aai/proTES",
        "ELIXIR Cloud & AAI": "https://elixir-cloud.dcc.sib.swiss/",
        "Tracker": "https://github.com/elixir-cloud-aai/proTES/issues",
    },
    packages=find_packages(),
    install_requires=install_requires,
    setup_requires=[
        "setuptools_git==1.2",
        "twine==3.8.0"
    ],
)
