package io.github.peyoteheadlights.companion;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class ProverTest {

    @Test
    void readsTheVersionFromThePlayersFile() {
        assertEquals("1.4", Prover.packVersion("{\"format\": 1, \"modpack\": {\"name\": \"S\", \"version\": \"1.4\"}}"));
    }

    @Test
    void anOddPlayersFileGivesNoVersion() {
        for (String odd : new String[] {"", "{", "[]", "{\"format\": 2, \"modpack\": {\"version\": \"1\"}}",
                "{\"format\": 1}", "{\"format\": 1, \"modpack\": {\"version\": \"" + "9".repeat(40) + "\"}}"}) {
            assertEquals("", Prover.packVersion(odd), odd);
        }
    }

    @Test
    void aMissingFileGetsAZeroProof() {
        Challenge c = new Challenge(new byte[16], List.of(new byte[32]), List.of(new Challenge.Spot(0, 0, 10)));
        Answer a = Prover.answer(c, Map.<String, Path>of(), "");
        assertEquals(List.of(false), a.loaded());
        assertArrayEquals(new byte[32], a.proofs().get(0));
    }

    @Test
    void aTooLongVersionIsLeftOutNotCut() {
        Challenge c = new Challenge(new byte[16], List.of(), List.of());
        assertEquals("", Prover.answer(c, Map.of(), "x".repeat(33)).packVersion());
    }
}
