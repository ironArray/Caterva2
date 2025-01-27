(Using-the-Web-client)=
# Using the Web client

Caterva2 subscribers also offer a way to browse roots and datasets without a dedicated client program.  At the HTTP root of a subscriber, you may find a Web client that provides some basic functionality.  This client is a work in progress, and it will be improved in future versions.

## Browsing datasets

First, make sure that test Caterva2 services are running (see [](Launching-Caterva2-services)) and point your Web browser to <http://localhost:8002>. You shall see a list of roots known to the subscriber, which only includes `foo` in our case:

<!-- For image options, see # (https://myst-parser.readthedocs.io/en/latest/syntax/images_and_figures.html)
-->

```{figure} images/web-initial-view.png
---
class: with-border
scale: 50%
---

Initial view of the roots known to the subscriber
```

Check `foo`, and you shall also get the list of datasets in the root, along with a search box.  Enabling an unsubscribed root will subscribe to it automatically.

Entering a string in the search box allows you to narrow the list of datasets down to those whose name contains that string, which is specially useful if you have many datasets.  Try entering `ds-` and clicking on "Search" to limit the list to example datasets (tip: entering `.b2` may help filtering out non-Blosc2 datasets).

```{figure} images/web-dataset-search.png
---
class: with-border
scale: 50%
---

Searching for datasets
```

## Accessing a dataset

Click on `foo/dir1/ds-2d.b2nd`, and you shall get the full name (path) of the dataset and a set of tabs.  The default one shows the main information of the dataset, including a download link for you to save the whole dataset to your computer, plus the metadata that we got from clients in previous sections, all in a nicely formatted table.

```{figure} images/web-main.png
---
class: with-border
scale: 50%
---

Viewing metadata
```

Depending on the dataset, you will also get "Data" and other tabs next to the "Main" tab that we just saw.  Click on the "Data" tab to see values in the dataset as a table where you can limit ranges in each dimension and select type fields.

```{figure} images/web-data.png
---
class: with-border
scale: 50%
---

Viewing data
```

Other tabs render certain files and datasets according to their declared or guessed content type.  Try entering `.md` in the search box, clicking on `foo/README.md`, then on the "Display" tab.  The dataset was recognized as a text file with Markdown-formatted content, which is rendered here.

```{figure} images/web-display-md.png
---
class: with-border
scale: 50%
---

Displaying Markdown text
```

Other datasets with a dedicated display are tomographies, i.e. stacks of images stored as 3D (greyscale) or 4D (color) arrays of unsigned integers.  Display support for other kinds of datasets may be added in the future.

You may find a richer variety of example datasets in the demo Caterva2 subscriber at <https://demo.caterva2.net/>.

## User authentication and personal space

Up until now we've seen the read-only operations that may be performed on a Caterva2 subscriber.  However, it also allows some useful writing operations that we'll see next.  These require user authentication to be enabled at the subscriber.

First, stop the current services and start new ones while setting the `CATERVA2_SECRET` environment variable, as detailed in [](Launching-Caterva2-services), then reload <http://localhost:8002> in your browser (or click on the Caterva2 logo).  You'll be greeted with a login screen; enter `user@example.com` as email address and `foobar11` as password, then click on "Login".

```{figure} images/web-login.png
---
class: with-border
scale: 50%
---

The login screen
```

The main Web client screen has some changes now: besides the indication of the logged in user, a new root called `@personal` just appeared, along with an upload icon.

This `@personal` is a pseudo-root offered to each user by the subscriber (i.e. it doesn't come from a publisher).  It allows the user to store private datasets for their use (as we'll see below), and each user can only see and access their own personal space.

Check both `foo` and `@personal`.  The resulting list of datasets is also slightly different, with an extra input box and each listed dataset being accompanied by a short tag.  Let's see the new stuff that you can do with all these!

```{figure} images/web-user.png
---
class: with-border
scale: 50%
---

The main screen showing new user features
```

## Uploading datasets

The upload icon below the list of roots allows you to upload new datasets to your personal space, either by clicking it and choosing a file, or by dragging and dropping the file on it.

Do upload the `root-example/ds-1d.b2nd` file from the Caterva2 source directory.  You'll see a new dataset `@personal/ds-1d.b2nd` pop up in the list of datasets, and its information will be shown.  Close to its "Download" link you'll see a "Delete" link, which will remove the dataset from your personal space (after asking for confirmation).  Try it, but don't remove the dataset yet!

```{figure} images/web-upload.png
---
class: with-border
scale: 50%
---

A newly uploaded dataset
```

You may now use the uploaded dataset as a normal one: download it, view its data and metadata, display it…

## Computing expressions on datasets

What about the input box that appeared below the search box?  It allows you to send commands to the subscriber.  The only kind of commands supported for the moment is the creation of **lazy expressions** involving the datasets accessible via the subscriber.  Such an expression is evaluated dynamically on the subscriber, so creating it is very cheap, and its result always reflects the latest state of the involved datasets.

First, let's select the dataset `foo/ds-1d.b2nd` and view its data: simply the range 0...9.  Now select the dataset `@personal/ds-1d.b2nd`: an array of the same shape, type and values.  Actually, the file that you uploaded *is* the source of `foo/ds-1d.b2nd` as served by your local publisher, hence the coincidence!

This means that we can safely create an expression that adds them together into a new dataset that we'll call `double`.  The command box accepts Python-like expressions, but how do we refer to the added datasets acting as operands to the addition?  That's the use of the tag that appears next to their name in the dataset list.  In our case, `foo/ds-1d.b2nd` is tagged as `a`, and `@personal/ds-1d.b2nd` as `k`, thus the command to be entered in the command box is `double = a + k`.  Try entering that command (mind that the tags may differ in your case) and click on "GO".  The resulting new dataset `@personal/double.b2nd` should be shown instantly.

```{figure} images/web-lazyexpr.png
---
class: with-border
scale: 50%
---

The newly created lazy expression
```

The dataset has very reduced metadata that just describes its shape, type, expression and operands.  However, you may still use it as any other dataset, e.g. to view its data (which will be computed on-the-fly), have it participate in other lazy expressions, or download it (with fully computed data) to your device.  As any dataset in your personal space, it can also be deleted.

Lazy expressions are a very versatile tool to have complex computations performed by a powerful and well-connected machine (the subscriber), and get the result to your device once satisfied with the result.  Many [arithmetic and reduction operations][b2-lazyexpr] are supported, just play with them and find out!

[b2-lazyexpr]: https://www.blosc.org/python-blosc2/getting_started/tutorials/02.lazyarray-expressions.html
    "LazyArray: Expressions containing NDArray objects (and others) (Python-Blosc2 documentation)"
