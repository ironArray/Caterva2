(caterva2.toml)=
# The `caterva2.toml` configuration file

We've seen that the `cat2cli` program accepts some command-line options to tune its operation (check the `--help` option).  This is even more important for services as we shall see in following sections.  Thus, Caterva2 programs support getting some settings from a TOML configuration file, by default `caterva2.toml` in the current directory (though you may override it with the `--conf` option).

The configuration file may hold settings for different programs, with a separate section for each program.  Thus, a program may check the file for its own settings, but also for those of other programs which may be of use to itself.  This allows compact configurations in a single file.

The configuration file may also hold settings for different instances of the same program (e.g. services of the same category).  To distinguish them, an arbitrary identifier may be provided to the program using the `--id` option (empty by default).  For instance:

```toml
[publisher]
# Settings for publisher with default ID.
[publisher.foo]
# Settings for publisher with `--id foo`.
[publisher.bar]
# Settings for publisher with `--id bar`.
```

Some of the supported settings will be explained in [](Running-independent-Caterva2-services).  See [caterva2.sample.toml](https://github.com/Blosc/Caterva2/blob/main/caterva2.sample.toml) in Caterva2's source for all possible settings and their purpose.
