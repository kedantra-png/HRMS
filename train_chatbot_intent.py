import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC
from sklearn.pipeline import Pipeline
import joblib

# 1) Load your labeled Excel (ID, Sentence, Intent)
df = pd.read_excel("static/chatbot_data_trani/chatbot_100k_sentences_dataset_labeled.xlsx")

texts = df["Sentence"].astype(str).tolist()
labels = df["Intent"].astype(str).tolist()

# 2) Build pipeline: TF‑IDF + LinearSVC
model = Pipeline([
    ("tfidf", TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=2,
        max_features=50000
    )),
    ("clf", LinearSVC())
])

# 3) Train
model.fit(texts, labels)

# 4) Save model
joblib.dump(model, "chatbot_intent_model.joblib")
print("Model saved to chatbot_intent_model.joblib")