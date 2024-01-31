# Using the Web client

Caterva2 subscribers also offer a way to browse roots and datasets without a dedicated client program. At the HTTP root of a subscriber, you may find a Web client that provides some basic functionality.  Make sure that test Caterva2 services are running (see [](Launching-Caterva2-services)) and point your Web browser to <http://localhost:8002/>. You shall see a list of roots known to the subscriber, which only includes `foo` in our case:

<!-- For image options, see # (https://myst-parser.readthedocs.io/en/latest/syntax/images_and_figures.html)
-->

```{figure} images/web-initial-view.png
---
class: with-border
scale: 50%
---

Initial view of the roots known to the subscriber
```

Click on `foo`, and you shall also get the list of datasets in the root, along with a search box.  Clicking on an unsubscribed root will subscribe to it automatically.

Entering a string in the search box allows you to narrow the list of datasets down to those whose name contains that string.  Try entering `ds-` and clicking on "Search" to limit the list to example datasets (tip: entering `.b2` may help filtering out non-Blosc2 datasets).

```{figure} images/web-dataset-search.png
---
class: with-border
scale: 50%
---

Searching for datasets
```

Choosing a dataset will show its metadata.  Click on `dir1/ds-2d.b2nd`, and you shall get the full name of the dataset plus the same metadata that we got from clients in previous examples, all in a nicely formatted table.

```{figure} images/web-metadata.png
---
class: with-border
scale: 50%
---

Viewing metadata
```

The Web client is a work in progress, and it will be improved in future versions.  For instance, it will allow you to download datasets in different formats, and to upload new datasets to the publisher.  It will also allow you to browse the contents of a dataset, and to plot it in different ways.
