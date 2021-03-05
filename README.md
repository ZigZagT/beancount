# Beancount

# Important Update
Beancount is now fully hosted on GitHub https://github.com/beancount/beancount

This fork here is not usefull anymore.


[![Build Status](https://travis-ci.com/BananaWanted/beancount.svg?branch=master)](https://travis-ci.com/BananaWanted/beancount)

This repo contains a fork of the [Beancount project][1]. Original documentations could be found [here on Google Doc](http://furius.ca/beancount/doc/index).

Branch *upstream* contains the upstream beancount source synced from [BitBucket repo][1] daily.

Branch *master* contains a fork from *upstream*, plus some additional changes described below.

## This Repo
- Provide a VCS interface in Git instead of in Mercurial.
    - The author actually has an [official GitHub Repo][2] but is quite out dated.
- Run tests regularly with updated dependencies (CI).
- Ship a [Docker image via DockerHub][3].
- Additional features/fixes not yet accepted by upstream (see complete list below)

Checkout line-by-line diff [here](https://github.com/BananaWanted/beancount/compare/upstream...master)

### Docker Support
You can run `bean-*` cli tools in the [Docker image][3] provided by this repo:
```
docker run -it --rm -v $(pwd):/data bananawanted/beancount bean-check --help
```

### Others
- CI setup running tests regularly.

[1]: https://bitbucket.org/blais/beancount/src/default/
[2]: https://github.com/beancount/beancount
[3]: https://hub.docker.com/r/bananawanted/beancount
