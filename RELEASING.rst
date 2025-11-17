Releasing a version
===================

Preliminaries
-------------

- Make sure that ``RELEASE_NOTES.md`` and ``ANNOUNCE.md`` are up to
  date with the latest news in the release.

- Check that ``__version__`` symbol in ``caterva2/__init__.py`` contains the correct info.

- Commit the changes with::

    $ git commit -a -m "Getting ready for release X.Y.Z"
    $ git push

For the release number, use the current date, but do not use 0's
(e.g. 2025.4.9, 2025.12.1, etc.).

Testing
-------

Follow the steps in ``README-DEVELOPERS.md`` file for locally creating and
installing the wheel, then test it::

  $ python -m pip install -e ".[tests,hdf5]"
  $ cd ..   # to avoid using the source code
  $ python -m caterva2.tests -v
  $ CATERVA2_SECRET="c2sikrit" python -m caterva2.tests -v
  $ cd -

Check that the examples in docstrings are up to date. You will
need to register a user in https://cat2.cloud/demo/ with
username 'user@example.com' and password 'foo'. Then, copy
the content of ``caterva2.sample.toml`` to ``caterva2.toml``
and run the following commands::

  $ rm -r _caterva2/
  $ cat2-admin adduser superuser@example.com foobarfoo -S
  $ CATERVA2_SECRET="123" cat2-server &

And experiment a bit with uploading, browsing and downloading files.

If the tests pass, you are ready to release.


Check documentation
-------------------

Check that the ``README.md`` and ``README-DEVELOPERS.md`` are consistent with this new release.


Tagging and releasing
---------------------

- Create a tag ``X.Y.Z`` from ``main``.  Use the next message::

    $ git tag -a vX.Y.Z -m "Tagging version X.Y.Z"

- Push the tag to the github repo::

    $ git push --tags

- If you happen to have to delete the tag, such as artifacts demonstrates a fault, first delete it locally::

    $ git tag --delete vX.Y.Z

  and then remotely on Github::

    $ git push --delete origin vX.Y.Z

- Create a new release by visiting https://github.com/ironArray/Caterva2/releases/new
  and add the release notes copying them from ``RELEASE_NOTES.md`` document.

Check that the release is available at https://pypi.org/project/caterva2/ and test it with::

  pip uninstall caterva2
  pip install caterva2[tests]
  python -m caterva2.tests
  CATERVA2_SECRET="c2sikrit" python -m caterva2.tests

Announcing
----------

- Send an announcement to the blosc and comp.compression mailing lists.
  Use the ``ANNOUNCE.md`` file as skeleton (likely as the definitive version).

- Announce the release in Linkedin/mastodon/bluesky from the @Blosc2 account.


Post-release actions
--------------------

- Edit ``__version__`` var in ``caterva2/__init__.py`` to increment the
  version to the next minor one (i.e. X.Y.Z --> X.Y.(Z+1).dev0).

- Create new headers for adding new features in ``RELEASE_NOTES.md``
  and add this place-holder instead::

    #XXX version-specific blurb XXX#

- Commit the changes::

  $ git commit -a -m"Post X.Y.Z release actions done"
  $ git push

That's all folks!
