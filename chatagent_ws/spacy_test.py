import spacy
from spacy.language import Language

# Load the Chinese model
nlp = spacy.load("zh_core_web_sm")


# Custom component to define sentence boundaries
@Language.component("custom_sentence_boundaries")
def custom_sentence_boundaries(doc):
    # Initially, assume no token starts a sentence
    for token in doc:
        token.is_sent_start = False

    # Set sentence starts only after "。"
    for i, token in enumerate(doc[:-1]):  # Skip the last token
        if token.text == "。" or token.text == "？" or token.text == "！" or token.text == "，":
            if i + 1 < len(doc):  # Ensure there's a next token
                doc[i + 1].is_sent_start = True

    # Ensure the first token starts a sentence
    if doc:
        doc[0].is_sent_start = True

    return doc


# Disable the default sentencizer and add the custom one
nlp.remove_pipe("senter") if "senter" in nlp.pipe_names else None
nlp.add_pipe("custom_sentence_boundaries", before="parser")

# Test text
text = "MULTAPPLY™ Waterborne Acrylic Gloss Enamel是$69.99，库存30"

# Process the text
doc = nlp(text)

# Extract full sentences
sentences = [sent.text.strip() for sent in doc.sents]

# Print the detected sentences
for i, sentence in enumerate(sentences):
    print(f"Sentence {i + 1}: {sentence}")