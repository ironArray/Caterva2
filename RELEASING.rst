Releasing a version
===================

Preliminaries
-------------

- Make sure that ``RELEASE_NOTES.md`` and ``ANNOUNCE.md`` are up to
  date with the latest news in the release.

- Check that *VERSION* symbol in caterva2/__init__.py contains the correct info.

- Commit the changes with::

    $ git commit -a -m "Getting ready for release X.Y.Z"
    $ git push


Testing
-------

Follow the steps in README-DEVELOPERS.md file for locally creating and
installing the wheel, then test it::

  $ cd ..   # to avoid using the source code
  $ python -c "import caterva2 as cat2; cat2.test(verbose=True)"
  $ cd -


You may want to use the existing services for testing the wheel::

  $ cd ..   # to avoid using the source code
  $ env CATERVA2_USE_EXTERNAL=1 python -c "import caterva2 as cat2; cat2.test(verbose=True)"
  $ cd -


Check documentation
-------------------

Check that the `README.md` and `README-DEVELOPERS.md` are consistent with this new release.


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

- Create a new release visiting https://github.com/Blosc/Caterva2/releases/new
  and add the release notes copying them from `RELEASE_NOTES.md` document.

Check that the release is available at https://pypi.org/project/caterva2/ and test it with::

  $ pip install caterva2
  $ python -c "import caterva2 as cat2; cat2.test(verbose=True)"


Announcing
----------

- Send an announcement to the blosc and comp.compression mailing lists.
  Use the ``ANNOUNCE.md`` file as skeleton (likely as the definitive version).

- Tweet/toot about it from the @Blosc2 account.


Post-release actions
--------------------

- Edit *__version__* var in caterva2/__init__.py to increment the
  version to the next minor one (i.e. X.Y.Z --> X.Y.(Z+1).dev0).

- Create new headers for adding new features in ``RELEASE_NOTES.md``
  and add this place-holder instead:

  #XXX version-specific blurb XXX#

- Commit the changes::

  $ git commit -a -m"Post X.Y.Z release actions done"
  $ git push

That's all folks!