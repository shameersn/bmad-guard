# <F-001> <Feature name>: technical summary

Written by Sloane from the approved architecture only. Source: <architecture file name>, version <x>.

## Technical

- **Stack and structure:** <the languages, frameworks and modules of the architecture that this feature's stories build in>
- **Interfaces:** <the API style, routes or contracts the architecture sets for this feature>
- **Data:** <storage, entities and migrations the architecture names for this feature>
- **Rules from the architecture:** <security, logging, error and test rules the stories of this feature must follow>

*How to use this template (delete every italic line and this note before saving):*

- *Take every line from the approved architecture. Never copy FRD text, never add a requirement, never guess: a line the architecture does not support is left out.*
- *Write plain bullets with a bold label. Do not use sub-headings under "Technical": the ready-for-dev gate reads the text under the "## Technical" heading and stops at the next heading.*
- *Keep it short: what a developer needs before starting any story of this feature, with a pointer to the architecture section for the rest. One file per feature, `stories/F-<nnn>/technical-summary.md`.*
- *If the architecture is silent on the feature, write "No feature-specific technical decisions in the architecture; see <section>." under the heading. Do not leave "## Technical" empty.*
- *Never write approval fields, a `Resolved:` line or an assumption tag in this file.*
