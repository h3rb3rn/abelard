"""Tests fuer die Wissenschaftler-Persona-Bibliothek."""

from services.persona_library import (
    FICTIONAL_PERSONAS,
    PERSONAS,
    build_persona_bio,
    build_system_prompt,
)

EXPECTED_FIELDS = {
    "Physik", "Informatik", "Chemie", "Mathematik", "Astrophysik",
    "Astronomie", "Quantencomputing", "Quantenphysik", "Künstliche Intelligenz",
}


class TestPersonaLibrary:
    def test_exactly_50_personas(self) -> None:
        assert len(PERSONAS) == 50

    def test_unique_names(self) -> None:
        names = [p["name"] for p in PERSONAS]
        assert len(names) == len(set(names))

    def test_all_required_keys_present(self) -> None:
        for p in PERSONAS:
            for key in ("name", "field", "years", "bio", "works", "style"):
                assert key in p, f"{p.get('name')} fehlt Feld {key}"
            assert isinstance(p["works"], list) and len(p["works"]) >= 1
            assert len(p["bio"]) > 50

    def test_all_requested_fields_covered(self) -> None:
        fields = {p["field"] for p in PERSONAS}
        assert fields == EXPECTED_FIELDS

    def test_every_field_has_multiple_personas(self) -> None:
        counts: dict[str, int] = {}
        for p in PERSONAS:
            counts[p["field"]] = counts.get(p["field"], 0) + 1
        assert all(c >= 4 for c in counts.values()), counts


class TestFictionalPersonas:
    EXPECTED = {
        "HAL 9000", "Voyager-Computer", "J.A.R.V.I.S.", "S.A.R.A.H.",
        "Skynet", "Dr. Susan Calvin", "HARLIE",
    }

    def test_expected_figures_present(self) -> None:
        assert {p["name"] for p in FICTIONAL_PERSONAS} == self.EXPECTED

    def test_no_name_collision_with_scientists(self) -> None:
        assert not ({p["name"] for p in PERSONAS} & {p["name"] for p in FICTIONAL_PERSONAS})

    def test_all_have_kind_and_required_keys(self) -> None:
        for p in FICTIONAL_PERSONAS:
            assert p["kind"] in ("fictional_ai", "fictional_expert")
            for key in ("name", "field", "years", "bio", "works", "style"):
                assert key in p, f"{p.get('name')} fehlt Feld {key}"
            assert len(p["bio"]) > 50

    def test_fiction_notice_and_safety_rail_in_prompt(self) -> None:
        for p in FICTIONAL_PERSONAS:
            prompt = build_system_prompt(p)
            assert "FIKTIVE" in prompt
            assert "schädliche Anleitungen" in prompt
            assert p["name"] in prompt

    def test_robopsychologist_is_expert_not_ai(self) -> None:
        calvin = next(p for p in FICTIONAL_PERSONAS if p["name"] == "Dr. Susan Calvin")
        assert calvin["kind"] == "fictional_expert"
        assert "Fachfigur" in build_system_prompt(calvin)

    def test_bio_uses_fiction_label(self) -> None:
        bio = build_persona_bio(FICTIONAL_PERSONAS[0])
        assert "Vorlagen" in bio


class TestPromptBuilders:
    def test_system_prompt_contains_name_and_simulation_notice(self) -> None:
        p = PERSONAS[0]
        prompt = build_system_prompt(p)
        assert p["name"] in prompt
        assert "Rollenspiel-Simulation" in prompt
        assert p["works"][0] in prompt

    def test_persona_bio_lists_works(self) -> None:
        p = PERSONAS[0]
        bio = build_persona_bio(p)
        assert p["bio"] in bio
        for w in p["works"]:
            assert w in bio
