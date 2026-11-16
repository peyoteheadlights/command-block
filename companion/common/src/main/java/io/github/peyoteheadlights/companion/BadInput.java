package io.github.peyoteheadlights.companion;

/** Thrown for any packet or settings file we refuse. The message is for logs only. */
public final class BadInput extends Exception {
    private static final long serialVersionUID = 1L;

    public BadInput(String message) {
        super(message);
    }
}
