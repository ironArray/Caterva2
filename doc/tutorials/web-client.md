## Using the Web client

TODO: The Web client is in constant development flux, do specify features and extend later on.

Caterva2 subscribers also offer a way to browse roots and datasets without a dedicated client program. At the HTTP root of a subscriber, you may find a Web client that provides some basic functionality.  Make sure that test Caterva2 services are running (see above) and point your Web browser to <http://localhost:8002/>.

You shall see a list of roots known to the subscriber, which only includes `foo` in our case.  Click on `foo`, and you shall also get the list of datasets in the root, along with a search box.  Entering a string in the box allows you to narrow the list of datasets down to those whose name contains that string.  Try entering `.b2` and clicking on "Search" to limit the list to Blosc2 datasets.

TODO: Capture of a filtered dataset list.

Choosing a dataset will show its metadata.  Click on `dir1/ds-2d.b2nd`, and you shall get the full name of the dataset plus the same metadata that we got from clients in previous examples, all in a nicely formatted table.

TODO: Capture of a metadata table.

