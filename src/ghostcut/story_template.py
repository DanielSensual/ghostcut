from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StorySection:
    name: str
    start_s: float
    end_s: float
    keywords: tuple[str, ...]
    preferred_layer: str = "a-roll"  # a-roll | b-roll | drone

    @property
    def target_duration(self) -> float:
        return max(0.0, self.end_s - self.start_s)


BASE_SECTIONS: tuple[StorySection, ...] = (
    StorySection(
        "Aerial Intro", 0.0, 8.0,
        ("aerial", "neighborhood", "community", "street", "overview"),
        preferred_layer="drone",
    ),
    StorySection(
        "Curb Appeal", 8.0, 18.0,
        ("exterior", "front", "curb", "driveway", "landscaping", "entrance"),
        preferred_layer="b-roll",
    ),
    StorySection(
        "Agent Hook", 18.0, 28.0,
        ("welcome", "introducing", "tour", "home", "property", "hello"),
        preferred_layer="a-roll",
    ),
    StorySection(
        "Main Living", 28.0, 42.0,
        ("living", "open", "space", "family", "natural light", "great room", "floor plan"),
        preferred_layer="a-roll",
    ),
    StorySection(
        "Kitchen & Dining", 42.0, 54.0,
        ("kitchen", "island", "countertops", "appliances", "dining", "pantry", "stainless"),
        preferred_layer="a-roll",
    ),
    StorySection(
        "Bedrooms & Baths", 54.0, 66.0,
        ("bedroom", "master", "bathroom", "closet", "suite", "ensuite", "vanity"),
        preferred_layer="a-roll",
    ),
    StorySection(
        "Outdoor / Pool", 66.0, 78.0,
        ("backyard", "patio", "pool", "outdoor", "lanai", "deck", "view", "covered"),
        preferred_layer="b-roll",
    ),
    StorySection(
        "CTA + Aerial Out", 78.0, 90.0,
        ("contact", "schedule", "showing", "call", "message", "reach out", "listing"),
        preferred_layer="drone",
    ),
)


def get_story_sections(target_duration: int) -> list[StorySection]:
    baseline = 90.0
    scale = max(0.2, target_duration / baseline)
    sections: list[StorySection] = []
    cursor = 0.0

    for base in BASE_SECTIONS:
        section_duration = base.target_duration * scale
        sections.append(
            StorySection(
                name=base.name,
                start_s=round(cursor, 3),
                end_s=round(cursor + section_duration, 3),
                keywords=base.keywords,
                preferred_layer=base.preferred_layer,
            )
        )
        cursor += section_duration

    return sections
