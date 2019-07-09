# Beancount

This repo contains a fork of the [Beancount](https://bitbucket.org/blais/beancount/) project.

Branch **upstream** contains the upstream beancount source synced from [BitBucket](https://bitbucket.org/blais/beancount/) daily.

Branch **master** contains a fork from **upstream**, plus some additional changes described below.

## Purposes of tis Repo
- Provide a VCS interface in Git instead of in Mercurial.
    - The author actually has an official [GitHub Repo](https://github.com/beancount/beancount) but is quite out dated.
- Integrate with CI, run tests regularly with updated dependencies.
- Ship a [Docker image](https://hub.docker.com/r/bananawanted/beancount).
- Additional features/fixes not yet accepted by upstream (see complete list below)

## List of Changes
Checkout line-by-line diff [here](https://github.com/BananaWanted/beancount/pull/1)

- [Docker image](https://hub.docker.com/r/bananawanted/beancount) contains bean-* cli tools.
- CI setup running tests regularly.
