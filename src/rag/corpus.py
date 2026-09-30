"""The corpus the license agent retrieves from: SPDX clause texts, one clause per passage.

Why this is RAG and the compatibility matrix is not is the whole point of having
both. The matrix is fifty rows, static and authoritative — it fits in a prompt, so
retrieving it would add latency and a new failure mode (the right row not coming
back) for nothing. License *texts* are the opposite: long, long-tailed, and only a
sentence or two of any of them matters to a given question. Fetching that sentence
on demand is what retrieval is for.

Passages are split by clause rather than by character count, because the unit a
compliance answer cites is "LGPL-2.1 section 6", not "characters 4000-4500". A
retriever that returns half a sentence cannot be quoted, and a citation is the whole
product here.

These are excerpts, not the full license texts, and every passage says so through
its `section`. They are the clauses that actually decide compatibility questions; a
license whose text is not here retrieves nothing, which surfaces as the agent having
no clause to cite rather than as a confidently invented one.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Passage:
    """One quotable clause, with enough identity to be cited."""

    license: str
    section: str
    text: str

    @property
    def id(self) -> str:
        return f"{self.license}#{self.section}"

    def render(self) -> str:
        return f"[{self.license} — {self.section}]\n{self.text}"


def clause(license: str, section: str, text: str) -> Passage:
    return Passage(license=license, section=section, text=" ".join(text.split()))


CLAUSES: tuple[Passage, ...] = (
    clause(
        "MIT",
        "permission grant",
        """Permission is hereby granted, free of charge, to any person obtaining a copy of
        this software and associated documentation files (the "Software"), to deal in the
        Software without restriction, including without limitation the rights to use, copy,
        modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
        subject to the following condition: the above copyright notice and this permission
        notice shall be included in all copies or substantial portions of the Software.""",
    ),
    clause(
        "BSD-3-Clause",
        "clause 3 (no endorsement)",
        """Neither the name of the copyright holder nor the names of its contributors may be
        used to endorse or promote products derived from this software without specific
        prior written permission.""",
    ),
    clause(
        "Apache-2.0",
        "section 4 (redistribution)",
        """You may reproduce and distribute copies of the Work or Derivative Works thereof in
        any medium, with or without modifications, provided that You give any other
        recipients of the Work or Derivative Works a copy of this License; cause any modified
        files to carry prominent notices stating that You changed the files; retain all
        copyright, patent, trademark, and attribution notices; and, if the Work includes a
        NOTICE file, include a readable copy of its attribution notices.""",
    ),
    clause(
        "Apache-2.0",
        "section 3 (patent grant and termination)",
        """Each Contributor hereby grants to You a perpetual, worldwide, non-exclusive,
        no-charge, royalty-free, irrevocable patent license to make, have made, use, offer to
        sell, sell, import, and otherwise transfer the Work. If You institute patent
        litigation against any entity alleging that the Work constitutes patent infringement,
        then any patent licenses granted to You under this License for that Work shall
        terminate as of the date such litigation is filed.""",
    ),
    clause(
        "GPL-2.0",
        "section 2b (reciprocal licensing)",
        """You must cause any work that you distribute or publish, that in whole or in part
        contains or is derived from the Program or any part thereof, to be licensed as a
        whole at no charge to all third parties under the terms of this License.""",
    ),
    clause(
        "GPL-3.0",
        "section 5 (conveying modified source)",
        """You may convey a work based on the Program provided that you license the entire
        work, as a whole, under this License to anyone who comes into possession of a copy.
        This License will therefore apply, along with any applicable section 7 additional
        terms, to the whole of the work, and all its parts, regardless of how they are
        packaged.""",
    ),
    clause(
        "GPL-3.0",
        "section 6 (conveying non-source forms)",
        """You may convey a covered work in object code form provided that you also convey
        the machine-readable Corresponding Source under the terms of this License, on a
        durable physical medium or through a network server at no charge.""",
    ),
    clause(
        "AGPL-3.0",
        "section 13 (remote network interaction)",
        """Notwithstanding any other provision of this License, if you modify the Program,
        your modified version must prominently offer all users interacting with it remotely
        through a computer network an opportunity to receive the Corresponding Source of your
        version by providing access to the Corresponding Source from a network server at no
        charge.""",
    ),
    clause(
        "LGPL-2.1",
        "section 6 (relinking obligation)",
        """As an exception to the Sections above, you may also combine or link a "work that
        uses the Library" with the Library to produce a work containing portions of the
        Library, and distribute that work under terms of your choice, provided that you
        accompany the work with the complete machine-readable source code for the Library
        including whatever changes were used, or use a suitable shared library mechanism, so
        that the user can modify the Library and then relink to produce a modified executable
        containing the modified Library.""",
    ),
    clause(
        "LGPL-3.0",
        "section 4 (combined works)",
        """You may convey a Combined Work under terms of your choice that, taken together,
        effectively do not restrict modification of the portions of the Library contained in
        the Combined Work, provided that you also give prominent notice that the Library is
        used in it, and either convey the Minimal Corresponding Source or use a suitable
        shared library mechanism.""",
    ),
    clause(
        "MPL-2.0",
        "section 3.3 (larger works, file-level copyleft)",
        """You may create and distribute a Larger Work under terms of Your choice, provided
        that You also comply with the requirements of this License for the Covered Software.
        The Covered Software — the files that carry this License — must remain under this
        License, but the Larger Work as a whole need not.""",
    ),
    clause(
        "EPL-2.0",
        "section 3.2 (source availability for distributed programs)",
        """If a Contributor Distributes the Program in any form, then the Program must also be
        made available as Source Code, and the Contributor must inform recipients how to
        obtain it in a reasonable manner on or through a medium customarily used for software
        exchange.""",
    ),
    clause(
        "CC-BY-SA-4.0",
        "section 3b (share-alike)",
        """If You Share Adapted Material You produce, the Adapter's License You apply must be
        a Creative Commons license with the same License Elements, this version or later, and
        You must include the text of, or the URI or hyperlink to, the Adapter's License You
        apply.""",
    ),
    clause(
        "BUSL-1.1",
        "additional use grant and change date",
        """The Licensor grants you the right to copy, modify, create derivative works, and
        make non-production use of the Licensed Work. Any production use requires a
        commercial license from the Licensor, unless it falls within the Additional Use
        Grant. On the Change Date, the Licensed Work becomes available under the Change
        License.""",
    ),
    clause(
        "SSPL-1.0",
        "section 13 (offering the program as a service)",
        """If you make the functionality of the Program available to third parties as a
        service, you must make the Service Source Code available under this License,
        including the source code for all programs used to make the Program available as a
        service — management software, user interfaces, APIs, monitoring, backup and hosting
        software.""",
    ),
    clause(
        "Unlicense",
        "public-domain dedication",
        """Anyone is free to copy, modify, publish, use, compile, sell, or distribute this
        software, either in source code form or as a compiled binary, for any purpose,
        commercial or non-commercial, and by any means.""",
    ),
)


def clauses_for(license: str) -> tuple[Passage, ...]:
    """Every passage whose license identifier starts the given one, version suffix aside.

    "GPL-3.0-only" and "GPL-3.0-or-later" both cite the GPL-3.0 text, and the corpus
    should not need one copy per SPDX spelling.
    """
    stem = license.split("-only")[0].split("-or-later")[0].lower()
    return tuple(passage for passage in CLAUSES if passage.license.lower() == stem)
