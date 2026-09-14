# Third-party notices

RottenLabz Herald's original source is MIT licensed. External software, service data, feeds, summaries, images and trademarks remain governed by their own licences and terms; listing them here does not place them under Herald's licence. This source candidate does not bundle a virtual environment, wheels or third-party binary libraries.

## Direct runtime software

| Package | Licence identified in the September 2026 audit | Upstream |
|---|---|---|
| discord.py | MIT | [project](https://github.com/Rapptz/discord.py) |
| python-dotenv | BSD-3-Clause | [project](https://github.com/theskumar/python-dotenv) |
| feedparser | BSD-2-Clause | [project](https://github.com/kurtmckee/feedparser) |
| requests | Apache-2.0 | [project](https://github.com/psf/requests) |

These are licence-family findings from the supplied dependency audit, not an exact inventory of the currently installed environment. The manifest contains ranges; the actual resolved artifacts and their included licence/NOTICE files govern redistribution. Runtime code also uses Python's standard library under the Python distribution's applicable licences.

## Expected transitive software

The audited resolver family includes aiohttp (Apache-2.0 and MIT), aiohappyeyeballs (PSF-2.0), aiosignal (Apache-2.0), attrs (MIT), frozenlist/multidict/propcache/yarl (Apache-2.0), charset-normalizer (MIT), idna (BSD-3-Clause), urllib3 (MIT), and certifi (MPL-2.0). Python-version/package markers may add typing-extensions, audioop-lts, async-timeout, feedparser-sgmllib or other parser support packages. No optional Discord voice extra is requested.

Generate the exact dependency inventory and SBOM from the tested release environment. Investigate unknown or changed licences, unexpected extras, and differences between platforms rather than treating this expected list as a complete bill of materials.

If distributing a container, bundled virtual environment, executable or dependency wheel set, collect and preserve each included artifact's copyright/licence notices and applicable Apache NOTICE content. For MPL-covered files such as certifi, preserve notices and provide the required source-availability information for the covered material. MIT/BSD/PSF dependencies also retain notice obligations. These requirements do not generally change the licence of independent Herald source, but redistribution must be assessed against the actual artifact.

## Supported public services

**GamerPower.** Every GamerPower-derived public alert must include an active clickable backlink to [GamerPower](https://www.gamerpower.com/), independently of the giveaway/claim URL. The implementation retains both links where applicable. Review the [GamerPower API page](https://www.gamerpower.com/api-read) for current service conditions. Do not claim its data as the project's own or sell API data without the necessary permission.

**Discord.** Herald interoperates with Discord through discord.py. Operators are responsible for their own application, permissions, accessible privacy notice and handling of API data under the [Discord Developer Terms](https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service) and [Developer Policy](https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy).

**Operator-selected RSS/Atom and private providers.** A feed's availability does not establish permission to store or republish its content. Operators must assess each source's rights/terms, attribution, data retention, deletion and redistribution conditions. The public examples use example.com placeholders and do not recommend restricted sources. The legacy removed integrations are not supported built-in services in this release; historical mentions in the changelog describe old releases only.

RottenLabz Herald is not affiliated with, sponsored by or endorsed by Discord or GamerPower. Third-party names are used descriptively; no endorsement or trademark clearance is implied.

## Release review

Run the inventory, security and SBOM gates in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md). The build environment could not independently retrieve current upstream package metadata or resolve the required version set; release qualification must complete those checks in a supported environment. Do not label this document an exhaustive, resolver-verified licence inventory.
