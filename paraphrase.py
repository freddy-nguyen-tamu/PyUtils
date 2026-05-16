import re
import random
from typing import List, Optional
from docx import Document


class RuleBasedParaphraser:
    """
    Pure rule-based paraphraser.
    No AI model is used.
    It relies on:
      - phrase substitutions
      - word substitutions
      - additive transformations (also / too / as well / additionally / moreover)
      - not only ... but also ...
      - fronted inversion: Not only did/was/has ...
      - with + noun + adjective
      - with + noun + verb-ing
      - with + noun + past participle
      - with + noun + prepositional phrase
      - with + noun + particle/state
      - adverb fronting
    """

    def __init__(self, seed: Optional[int] = None):
        self.random = random.Random(seed)

        self.phrase_map = {
            "in order to": ["to", "so as to"],
            "a lot of": ["many", "numerous"],
            "because of": ["due to", "as a result of"],
            "for example": ["for instance"],
            "in addition": ["additionally", "furthermore", "moreover"],
            "at the same time": ["simultaneously", "meanwhile"],
            "as soon as": ["once"],
            "in the end": ["eventually", "ultimately"],
            "on the other hand": ["however", "by contrast"],
        }

        self.word_map = {
            "also": ["too", "as well", "additionally"],
            "important": ["significant", "essential", "crucial"],
            "big": ["large", "substantial", "considerable"],
            "small": ["little", "minor", "slight"],
            "good": ["beneficial", "favorable", "positive"],
            "bad": ["negative", "unfavorable", "harmful"],
            "show": ["demonstrate", "display", "reveal"],
            "use": ["utilize", "employ"],
            "help": ["assist", "support"],
            "make": ["create", "produce"],
            "get": ["obtain", "receive"],
            "buy": ["purchase"],
            "need": ["require"],
            "start": ["begin", "commence"],
            "end": ["finish", "conclude"],
            "change": ["alter", "modify", "transform"],
            "idea": ["concept", "notion"],
            "result": ["outcome", "consequence"],
            "many": ["numerous", "a large number of"],
            "often": ["frequently", "regularly"],
            "usually": ["generally", "typically"],
            "quickly": ["rapidly", "swiftly"],
            "slowly": ["gradually"],
            "very": ["highly", "extremely"],
            "happy": ["pleased", "delighted"],
            "sad": ["unhappy", "downcast"],
            "easy": ["simple", "straightforward"],
            "hard": ["difficult", "challenging"],
        }

        self.common_adjectives = {
            "open", "closed", "ready", "silent", "calm", "bright",
            "careful", "confident", "nervous", "happy", "sad", "busy",
            "red", "pale", "dark", "cold", "warm", "quiet", "angry",
            "tired", "serious", "upset", "still", "empty", "full"
        }

    def paraphrase(self, text: str, n: int = 1) -> List[str]:
        results = []
        seen = set()

        attempts = max(n * 8, 20)
        for _ in range(attempts):
            current = text

            current = self._replace_phrases(current)
            current = self._apply_transformations(current)
            current = self._replace_words(current)
            current = self._cleanup(current)

            if current and current != text and current not in seen:
                seen.add(current)
                results.append(current)

            if len(results) >= n:
                break

        if not results:
            fallback = self._cleanup(self._replace_words(text))
            if fallback:
                results.append(fallback)

        return results[:n]

    def paraphrase_one(self, text: str) -> str:
        outputs = self.paraphrase(text, n=1)
        return outputs[0] if outputs else text

    def _apply_transformations(self, text: str) -> str:
        transforms = [
            self._transform_also_to_not_only_parallel,
            self._transform_also_to_fronted_linker,
            self._transform_end_additive,
            self._transform_not_only_inversion_from_clause_pairs,
            self._transform_with_adjective,
            self._transform_with_verbing,
            self._transform_with_past_participle,
            self._transform_with_prepositional_phrase,
            self._transform_with_particle_state,
            self._transform_predicative_adjective_compression,
            self._transform_fronted_adverb,
        ]

        self.random.shuffle(transforms)
        for fn in transforms:
            new_text = fn(text)
            if new_text != text:
                text = new_text
        return text

    def _replace_phrases(self, text: str) -> str:
        for phrase, options in self.phrase_map.items():
            pattern = re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
            if pattern.search(text) and self.random.random() < 0.6:
                replacement = self.random.choice(options)
                text = pattern.sub(
                    lambda m: self._preserve_case(m.group(0), replacement),
                    text,
                    count=1,
                )
        return text

    def _replace_words(self, text: str) -> str:
        def repl(match: re.Match) -> str:
            word = match.group(0)
            lower = word.lower()
            if lower in self.word_map and self.random.random() < 0.35:
                replacement = self.random.choice(self.word_map[lower])
                return self._preserve_case(word, replacement)
            return word

        return re.sub(r"\b[A-Za-z]+\b", repl, text)

    def _transform_also_to_not_only_parallel(self, text: str) -> str:
        patterns = [
            re.compile(
                r"\b(?P<subj>[A-Z][a-z]+|I|You|We|They|He|She|It)\s+"
                r"(?P<be>am|is|are|was|were)\s+also\s+(?P<rest>[^.,;!?]+)",
                re.IGNORECASE
            ),
            re.compile(
                r"\b(?P<subj>[A-Z][a-z]+|I|You|We|They|He|She|It)\s+also\s+"
                r"(?P<verb>\w+)\s+(?P<rest>[^.,;!?]+)",
                re.IGNORECASE
            ),
            re.compile(
                r"\b(?P<subj>[A-Z][a-z]+|I|You|We|They|He|She|It)\s+"
                r"(?P<aux>can|could|will|would|should|must|may|might|has|have|had|do|does|did)\s+"
                r"also\s+(?P<verb>\w+)\s+(?P<rest>[^.,;!?]+)",
                re.IGNORECASE
            ),
        ]

        for pattern in patterns:
            m = pattern.search(text)
            if not m:
                continue

            gd = m.groupdict()

            if "be" in gd:
                subj = gd["subj"]
                be = gd["be"]
                rest = gd["rest"].strip()
                replacement = f"{subj} is not only {rest} but also {rest}"
                if be.lower() != "is":
                    replacement = f"{subj} {be} not only {rest} but also {rest}"
                return text[:m.start()] + replacement + text[m.end():]

            if "aux" in gd:
                subj = gd["subj"]
                aux = gd["aux"]
                verb = gd["verb"]
                rest = gd["rest"].strip()
                replacement = f"{subj} {aux} not only {verb} {rest} but also {verb} {rest}"
                return text[:m.start()] + replacement + text[m.end():]

            subj = gd["subj"]
            verb = gd["verb"]
            rest = gd["rest"].strip()
            replacement = f"{subj} not only {verb} {rest} but also {verb} {rest}"
            return text[:m.start()] + replacement + text[m.end():]

        return text

    def _transform_also_to_fronted_linker(self, text: str) -> str:
        pattern = re.compile(
            r"^(?P<subj>[A-Z][a-z]+|I|You|We|They|He|She|It)\s+also\s+(?P<rest>.+)$",
            re.IGNORECASE
        )
        m = pattern.search(text.strip())
        if m and self.random.random() < 0.5:
            linker = self.random.choice(["Additionally", "Moreover", "Furthermore", "Also"])
            subj = m.group("subj")
            rest = m.group("rest").strip()
            return f"{linker}, {subj.lower() if linker != 'Also' else subj} {rest}"
        return text

    def _transform_end_additive(self, text: str) -> str:
        pattern = re.compile(
            r"\b(?P<subj>[A-Z][a-z]+|I|You|We|They|He|She|It)\s+also\s+(?P<rest>[^.!?]+)(?P<end>[.!?]?)$",
            re.IGNORECASE
        )
        m = pattern.search(text.strip())
        if m and self.random.random() < 0.5:
            subj = m.group("subj")
            rest = m.group("rest").strip()
            end = m.group("end") or ""
            tail = self.random.choice(["too", "as well"])
            return f"{subj} {rest} {tail}{end}"
        return text

    def _transform_not_only_inversion_from_clause_pairs(self, text: str) -> str:
        patterns = [
            re.compile(
                r"^(?P<subj>[A-Z][a-z]+|he|she|it|they|we|you|I)\s+"
                r"(?P<be>am|is|are|was|were)\s+(?P<comp1>[^,]+),\s+and\s+"
                r"(?P=subj)\s+also\s+(?P<verb2>\w+)\s+(?P<rest2>[^.!?]+)(?P<end>[.!?]?)$",
                re.IGNORECASE
            ),
            re.compile(
                r"^(?P<subj>[A-Z][a-z]+|he|she|it|they|we|you|I)\s+"
                r"(?P<verb1>\w+)\s+(?P<rest1>[^,]+),\s+and\s+"
                r"(?P=subj)\s+also\s+(?P<verb2>\w+)\s+(?P<rest2>[^.!?]+)(?P<end>[.!?]?)$",
                re.IGNORECASE
            ),
        ]

        for pattern in patterns:
            m = pattern.search(text.strip())
            if not m:
                continue

            gd = m.groupdict()
            subj = gd["subj"]
            end = gd.get("end") or ""

            if "be" in gd:
                be = gd["be"]
                comp1 = gd["comp1"].strip()
                verb2 = gd["verb2"]
                rest2 = gd["rest2"].strip()
                return f"Not only {be} {subj.lower()} {comp1}, but {subj.lower()} also {verb2} {rest2}{end}"

            verb1 = gd["verb1"]
            rest1 = gd["rest1"].strip()
            verb2 = gd["verb2"]
            rest2 = gd["rest2"].strip()

            aux = self._simple_do_support(subj, verb1)
            base1 = self._to_base_verb(verb1)
            return f"Not only {aux} {subj.lower()} {base1} {rest1}, but {subj.lower()} also {verb2} {rest2}{end}"

        return text

    def _transform_with_adjective(self, text: str) -> str:
        pattern = re.compile(
            r"^(?P<main>.+?),\s+and\s+(?P<poss>his|her|their|my|our|your)\s+"
            r"(?P<noun>\w+)\s+(?:was|were)\s+(?P<adj>\w+)(?P<end>[.!?]?)$",
            re.IGNORECASE
        )
        m = pattern.search(text.strip())
        if m:
            adj = m.group("adj").lower()
            if adj in self.common_adjectives:
                return (
                    f"{m.group('main')}, with {m.group('poss')} "
                    f"{m.group('noun')} {m.group('adj')}{m.group('end')}"
                )
        return text

    def _transform_with_verbing(self, text: str) -> str:
        pattern = re.compile(
            r"^(?P<main>.+?),\s+and\s+(?P<noun>[\w\s]+?)\s+(?:was|were)\s+"
            r"(?P<verbing>\w+ing(?:\s+[^.!?]+)?)(?P<end>[.!?]?)$",
            re.IGNORECASE
        )
        m = pattern.search(text.strip())
        if m:
            main = m.group("main").strip()
            noun = m.group("noun").strip()
            verbing = m.group("verbing").strip()
            end = m.group("end")
            return f"{main}, with {noun} {verbing}{end}"
        return text

    def _transform_with_past_participle(self, text: str) -> str:
        pattern = re.compile(
            r"^(?P<main>.+?),\s+and\s+(?P<noun>[\w\s]+?)\s+(?:was|were)\s+"
            r"(?P<pp>\w+(?:ed|en|wn|ught|pt|lt|nt)(?:\s+\w+)*)"
            r"(?P<end>[.!?]?)$",
            re.IGNORECASE
        )
        m = pattern.search(text.strip())
        if m:
            return (
                f"{m.group('main')}, with {m.group('noun').strip()} "
                f"{m.group('pp').strip()}{m.group('end')}"
            )
        return text

    def _transform_with_prepositional_phrase(self, text: str) -> str:
        pattern = re.compile(
            r"^(?P<main>.+?),\s+and\s+(?P<noun>[\w\s]+?)\s+(?:was|were)\s+"
            r"(?P<prep>in|on|at|under|over|behind|beside|near|by|inside|outside)\s+(?P<rest>[^.!?]+)"
            r"(?P<end>[.!?]?)$",
            re.IGNORECASE
        )
        m = pattern.search(text.strip())
        if m:
            return (
                f"{m.group('main')}, with {m.group('noun').strip()} "
                f"{m.group('prep')} {m.group('rest').strip()}{m.group('end')}"
            )
        return text

    def _transform_with_particle_state(self, text: str) -> str:
        pattern = re.compile(
            r"^(?P<main>.+?),\s+and\s+(?P<noun>[\w\s]+?)\s+(?:was|were)\s+"
            r"(?P<particle>on|off|up|down|out|in)(?P<end>[.!?]?)$",
            re.IGNORECASE
        )
        m = pattern.search(text.strip())
        if m:
            return (
                f"{m.group('main')}, with {m.group('noun').strip()} "
                f"{m.group('particle')}{m.group('end')}"
            )
        return text

    def _transform_predicative_adjective_compression(self, text: str) -> str:
        pattern = re.compile(
            r"^(?P<main>.+?)\s+and\s+was\s+(?P<adj>\w+)(?P<end>[.!?]?)$",
            re.IGNORECASE
        )
        m = pattern.search(text.strip())
        if m and m.group("adj").lower() in self.common_adjectives:
            return f"{m.group('main')}, {m.group('adj')}{m.group('end')}"
        return text

    def _transform_fronted_adverb(self, text: str) -> str:
        pattern = re.compile(
            r"^(?P<subj>[A-Z][a-z]+|I|You|We|They|He|She|It)\s+"
            r"(?P<adv>quickly|slowly|carefully|suddenly|quietly|usually|often|generally|typically|frequently)\s+"
            r"(?P<rest>[^.!?]+)(?P<end>[.!?]?)$",
            re.IGNORECASE
        )
        m = pattern.search(text.strip())
        if m and self.random.random() < 0.7:
            adv = self._capitalize(m.group("adv"))
            subj = m.group("subj").lower()
            rest = m.group("rest").strip()
            end = m.group("end")
            return f"{adv}, {subj} {rest}{end}"
        return text

    def _to_base_verb(self, verb: str) -> str:
        lower = verb.lower()

        irregular = {
            "went": "go", "came": "come", "wrote": "write", "spoke": "speak",
            "ran": "run", "sang": "sing", "bought": "buy", "brought": "bring",
            "made": "make", "did": "do", "had": "have", "saw": "see",
            "took": "take", "gave": "give", "found": "find", "thought": "think",
            "felt": "feel", "left": "leave", "told": "tell", "kept": "keep",
        }
        if lower in irregular:
            return irregular[lower]

        if lower.endswith("ies") and len(lower) > 3:
            return lower[:-3] + "y"
        if lower.endswith("es") and len(lower) > 2:
            return lower[:-2]
        if lower.endswith("s") and len(lower) > 1:
            return lower[:-1]
        if lower.endswith("ed") and len(lower) > 2:
            return lower[:-2]

        return lower

    def _simple_do_support(self, subj: str, verb: str) -> str:
        if verb.lower().endswith("ed") or verb.lower() in {
            "went", "came", "wrote", "spoke", "ran", "sang", "bought",
            "brought", "made", "did", "had", "saw", "took", "gave",
            "found", "thought", "felt", "left", "told", "kept"
        }:
            return "did"

        if subj.lower() in {"he", "she", "it"} or re.match(r"^[A-Z][a-z]+$", subj):
            return "does"

        return "do"

    def _preserve_case(self, original: str, replacement: str) -> str:
        if original.isupper():
            return replacement.upper()
        if original[:1].isupper():
            return replacement.capitalize()
        return replacement

    def _capitalize(self, word: str) -> str:
        return word[:1].upper() + word[1:] if word else word

    def _cleanup(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        text = re.sub(r",\s*,", ", ", text)
        text = text.strip()

        if text and text[0].islower():
            text = text[0].upper() + text[1:]

        return text


def paraphrase_docx(input_path: str, output_path: str, seed: Optional[int] = 7) -> None:
    """
    Read a Word document, paraphrase each non-empty paragraph,
    and save the result to a new Word document.
    """
    paraphraser = RuleBasedParaphraser(seed=seed)
    input_doc = Document(input_path)
    output_doc = Document()

    for para in input_doc.paragraphs:
        original_text = para.text.strip()

        if not original_text:
            output_doc.add_paragraph("")
            continue

        # Keep headings roughly preserved
        style_name = para.style.name if para.style else None

        paraphrased_text = paraphraser.paraphrase_one(original_text)

        new_para = output_doc.add_paragraph(paraphrased_text)
        if style_name:
            try:
                new_para.style = style_name
            except Exception:
                pass

    output_doc.save(output_path)


if __name__ == "__main__":
    input_file = "input.docx"
    output_file = "output_paraphrased.docx"

    paraphrase_docx(input_file, output_file, seed=7)
    print(f"Done. Saved paraphrased document to: {output_file}")