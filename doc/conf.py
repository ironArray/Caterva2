# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "Caterva2"
copyright = "2024, ironArray SLU"
author = "ironArray SLU"
release = "0.3.dev0"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autosummary",
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "numpydoc",
    "myst_parser",
    "sphinx_paramlinks",
    "sphinx_design",
    "nbsphinx",
]

autosummary_generate = True  # Enable auto generation of stub files

myst_enable_extensions = [
    "html_image",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_static_path = ["_static"]
source_suffix = [".rst", ".md"]
html_theme = "pydata_sphinx_theme"
html_css_files = [
    "css/custom.css",
]
html_favicon = "_static/logo-caterva2-16x16.png"

# Theme options are theme-specific and customize the look and feel of a theme
# further.  For a list of options available for each theme, see the
# documentation.
html_theme_options = {
    "github_url": "https://github.com/ironArray/Caterva2",
    "icon_links": [
        {
            "name": "ironArray Web Site",
            "url": "https://ironarray.io/",
            "icon": "_static/ironArray-icon2-128px.png",
            "type": "local",
        },
    ],
    "collapse_navigation": True,
    "navigation_with_keys": False,
}

# The name of an image file (relative to this directory) to place at the top
# of the sidebar.
html_logo = "_static/logo-caterva2-horizontal-half.png"
