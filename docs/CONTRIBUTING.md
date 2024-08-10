# How to contribute

Our software is open source so you can solve your own problems without needing help from others. And if you solve a problem and are so kind, you can upstream it for the rest of the world to use. Check out our [post about externalization](https://blog.comma.ai/a-2020-theme-externalization/).

Development is coordinated through [Discord](https://discord.comma.ai) and GitHub.

### Getting Started

* Setup your [development environment](../tools/)
* Read about the [development workflow](WORKFLOW.md)
* Join our [Discord](https://discord.comma.ai)
* Docs are at https://docs.comma.ai and https://blog.comma.ai

## What contributions are we looking for?

**openpilot's priorities are [safety](SAFETY.md), stability, quality, and features, in that order.**
openpilot is part of comma's mission to *solve self-driving cars while delivering shippable intermediaries*, and all development is towards that goal. 

### What gets merged?

The probability of a pull request being merged is a function of its value to the project and the effort it will take us to get it merged.
If a PR offers *some* value but will take lots of time to get merged, it will be closed.
Simple, well-tested bug fixes are the easiest to merge, and new features are the hardest to get merged. 

All of these are examples of good PRs:
* typo fix: https://github.com/commaai/openpilot/pull/30678
* removing unused code: https://github.com/commaai/openpilot/pull/30573
* simple car model port: https://github.com/commaai/openpilot/pull/30245
* car brand port: https://github.com/commaai/openpilot/pull/23331

### What doesn't get merged?

* **style changes**: code is art, and it's up to the author to make it beautiful 
* **500+ line PRs**: clean it up, break it up into smaller PRs, or both
* **PRs without a clear goal**: every PR must have a singular and clear goal
* **UI design**: we do not have a good review process for this yet
* **New features**: We believe openpilot is mostly feature-complete, and the rest is a matter of refinement and fixing bugs. As a result of this, most feature PRs will be immediately closed, however the beauty of open source is that forks can and do offer features that upstream openpilot doesn't.
* **Negative expected value**: This a class of PRs that makes an improvement, but the risk or validation costs more than the improvement. The risk can be mitigated by first getting a failing test merged.

### First contribution

[Bounties](https://comma.ai/bounties) are the best place to get started.
There's lot of bounties that don't require a comma 3/3X or a car.

## Pull Requests

Pull requests should be against the master branch.

A good pull request has all of the following:
* a clearly stated purpose
* every line changed directly contributes to the stated purpose
* verification, i.e. how did you test your PR?
* justification
  * if you've optimized something, post benchmarks to prove it's better
  * if you've improved your car's tuning, post before and after plots
* passes the CI tests

## Contributing without Code

* Report bugs in GitHub issues.
* Report driving issues in the `#driving-feedback` Discord channel.
* Consider opting into driver camera uploads to improve the driver monitoring model.
* Connect your device to Wi-Fi regularly, so that we can pull data for training better driving models.
* Run the `nightly` branch and report issues. This branch is like `master` but it's built just like a release.
* Annotate images in the [comma10k dataset](https://github.com/commaai/comma10k).
