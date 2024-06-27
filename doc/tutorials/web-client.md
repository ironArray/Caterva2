(Using-the-Web-client)=
# Using the Web client

Caterva2 subscribers also offer a way to browse roots and datasets without a dedicated client program.  At the HTTP root of a subscriber, you may find a Web client that provides some basic functionality.  This client is a work in progress, and it will be improved in future versions.

## Browsing datasets

First, make sure that test Caterva2 services are running (see [](Launching-Caterva2-services)) and point your Web browser to <http://localhost:8002/>. You shall see a list of roots known to the subscriber, which only includes `foo` in our case:

<!-- For image options, see # (https://myst-parser.readthedocs.io/en/latest/syntax/images_and_figures.html)
-->

```{figure} images/web-initial-view.png
---
class: with-border
scale: 50%
---

Initial view of the roots known to the subscriber #TODO update
```

Check `foo`, and you shall also get the list of datasets in the root, along with a search box.  Enabling an unsubscribed root will subscribe to it automatically.

Entering a string in the search box allows you to narrow the list of datasets down to those whose name contains that string, which is specially useful if you have many datasets.  Try entering `ds-` and clicking on "Search" to limit the list to example datasets (tip: entering `.b2` may help filtering out non-Blosc2 datasets).

```{figure} images/web-dataset-search.png
---
class: with-border
scale: 50%
---

Searching for datasets #TODO update
```

## Accessing a dataset

Click on `foo/dir1/ds-2d.b2nd`, and you shall get the full name (path) of the dataset and a set of tabs.  The default one shows the main information of the dataset, including a download link for you to save the whole dataset to your computer, plus the metadata that we got from clients in previous sections, all in a nicely formatted table.

```{figure} images/web-main.png
---
class: with-border
scale: 50%
---

Viewing metadata #TODO
```

Depending on the dataset, you will also get "Data" and other tabs next to the "Main" tab that we just saw.  Click on the "Data" tab to see values in the dataset as a table where you can limit ranges in each dimension and select type fields.

```{figure} images/web-data.png
---
class: with-border
scale: 50%
---

Viewing data #TODO
```

Other tabs render certain files and datasets according to their declared or guessed content type.  Try entering `.md` in the search box, clicking on `foo/README.md`, then on the "Display" tab.  The dataset was recognized as a text file with Markdown-formatted content, which is rendered here.

```{figure} images/web-display-md.png
---
class: with-border
scale: 50%
---

Displaying Markdown text #TODO
```

Other datasets with a dedicated display are tomographies, i.e. stacks of images stored as 3D (greyscale) or 4D (color) arrays of unsigned integers.  Display support for other kinds of datasets may be added in the future.

You may find a richer variety of example datasets in the demo Caterva2 subscriber at <https://demo.caterva2.net/>.

## User authentication and scratch space

TODO

## Uploading datasets

TODO

## Computing expressions on datasets

TODO
