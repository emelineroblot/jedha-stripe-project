"""
Démo — modèle de détection de fraude sur les données synthétiques.

Entrée  : data/_ground_truth_fraud.csv (features figées au moment du scoring + vérité terrain latente)
Sortie  : ml/output/metrics.json, ml/output/shap_summary.png, ml/output/pr_curve.png,
          ml/output/model.json (XGBoost), run MLflow local si mlflow est installé.

Ce que la démo illustre (voir docs/07_ml_integration.md) :
  - AUC-PR comme métrique principale sur une classe rare
  - politique d'action (allow / 3DS / review / block) dérivée de la probabilité
  - explicabilité SHAP globale et par transaction (stockée dans ml_features.prediction.top_shap)
  - enregistrement dans un registry (MLflow) avec métriques et artefacts

Usage : python ml/train_fraud_demo.py [--data data/_ground_truth_fraud.csv]
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import (average_precision_score, precision_recall_curve,
                             roc_auc_score, classification_report)
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

parser = argparse.ArgumentParser()
parser.add_argument("--data", default="data/_ground_truth_fraud.csv")
parser.add_argument("--out", default="ml/output")
args = parser.parse_args()
os.makedirs(args.out, exist_ok=True)

# ─── Données ─────────────────────────────────────────────────────────────────

df = pd.read_csv(args.data)
FEATURES = ["amount_zscore", "transactions_last_1h", "transactions_last_24h", "avg_amount_30d",
            "distinct_countries_30d", "geo_mismatch", "night_transaction", "velocity_score",
            "device_seen_before", "shared_card_fingerprint", "ip_reputation_score"]
X = df[FEATURES].astype(float)
y = df["is_fraud_true"].astype(int)
print(f"{len(df)} transactions, {y.sum()} fraudes ({100 * y.mean():.1f} %)")

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3, stratify=y, random_state=42)

# ─── Modèle ──────────────────────────────────────────────────────────────────

model = XGBClassifier(
    n_estimators=200, max_depth=4, learning_rate=0.05, subsample=0.9, colsample_bytree=0.9,
    scale_pos_weight=(len(y_train) - y_train.sum()) / y_train.sum(),   # ré-équilibrage
    eval_metric="aucpr", random_state=42,
)
model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
proba = model.predict_proba(X_test)[:, 1]

# ─── Métriques ───────────────────────────────────────────────────────────────

auc_pr = average_precision_score(y_test, proba)
auc_roc = roc_auc_score(y_test, proba)
precision, recall, thresholds = precision_recall_curve(y_test, proba)

# Politique d'action : mêmes seuils que le service de scoring
def action(p):
    return "block" if p >= 0.85 else "review" if p >= 0.60 else "challenge_3ds" if p >= 0.30 else "allow"

actions = pd.Series([action(p) for p in proba], index=y_test.index)
policy = (pd.DataFrame({"action": actions, "fraud": y_test})
          .groupby("action")["fraud"].agg(n="count", frauds="sum")
          .assign(precision_pct=lambda d: (100 * d.frauds / d.n).round(1))
          .reindex(["block", "review", "challenge_3ds", "allow"]).dropna())

# Recall à précision >= 0.8 (métrique de promotion)
mask = precision[:-1] >= 0.8
recall_at_p80 = float(recall[:-1][mask].max()) if mask.any() else 0.0

metrics = {
    "n_train": int(len(y_train)), "n_test": int(len(y_test)), "fraud_rate": round(float(y.mean()), 4),
    "auc_pr": round(float(auc_pr), 4), "auc_roc": round(float(auc_roc), 4),
    "recall_at_precision_0.8": round(recall_at_p80, 4),
    "policy": policy.reset_index().to_dict(orient="records"),
}
print(f"\nAUC-PR  = {auc_pr:.3f}   (baseline aléatoire = {y_test.mean():.3f})")
print(f"AUC-ROC = {auc_roc:.3f}")
print(f"Recall à précision >= 0.8 : {recall_at_p80:.2f}")
print("\nPolitique d'action sur le jeu de test :")
print(policy.to_string())
print("\n" + classification_report(y_test, proba >= 0.6, target_names=["légitime", "fraude"], digits=3))

# ─── Explicabilité SHAP ──────────────────────────────────────────────────────

try:
    import shap
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_test)

    plt.figure()
    shap.summary_plot(shap_values, X_test, show=False, max_display=11)
    plt.tight_layout()
    plt.savefig(os.path.join(args.out, "shap_summary.png"), dpi=130)
    plt.close()

    # Top 3 facteurs pour la transaction la plus risquée — ce qui est stocké dans ml_features
    i = int(np.argmax(proba))
    top3 = sorted(zip(FEATURES, shap_values[i]), key=lambda t: -abs(t[1]))[:3]
    metrics["example_top_shap"] = {"fraud_probability": round(float(proba[i]), 4),
                                   "top_shap": [{"feature": f, "value": round(float(v), 3)} for f, v in top3]}
    print("\nTransaction la plus risquée du test — top 3 SHAP :", metrics["example_top_shap"])

    importance = pd.Series(np.abs(shap_values).mean(axis=0), index=FEATURES).sort_values(ascending=False)
    metrics["global_importance"] = importance.round(4).to_dict()
    print("\nImportance globale (|SHAP| moyen) :")
    print(importance.round(3).to_string())
except ImportError:
    print("\n(shap / matplotlib non installés : explicabilité ignorée)")

# Courbe précision-rappel
try:
    import matplotlib.pyplot as plt
    plt.figure(figsize=(5, 4))
    plt.plot(recall, precision)
    plt.xlabel("Recall"); plt.ylabel("Precision"); plt.title(f"PR curve — AUC-PR = {auc_pr:.3f}")
    plt.grid(alpha=0.3); plt.tight_layout()
    plt.savefig(os.path.join(args.out, "pr_curve.png"), dpi=130)
    plt.close()
except ImportError:
    pass

# ─── Registry ────────────────────────────────────────────────────────────────

model.save_model(os.path.join(args.out, "model.json"))
with open(os.path.join(args.out, "metrics.json"), "w", encoding="utf-8") as f:
    json.dump(metrics, f, indent=2, ensure_ascii=False)

try:
    import mlflow
    mlflow.set_tracking_uri(f"file:{os.path.join(args.out, 'mlruns')}")
    mlflow.set_experiment("fraud-detection")
    with mlflow.start_run(run_name="fraud-xgb-demo"):
        mlflow.log_params({"n_estimators": 200, "max_depth": 4, "learning_rate": 0.05, "features": len(FEATURES)})
        mlflow.log_metrics({"auc_pr": auc_pr, "auc_roc": auc_roc, "recall_at_p80": recall_at_p80})
        mlflow.log_artifacts(args.out)
    print(f"\nRun MLflow enregistré dans {args.out}/mlruns")
except ImportError:
    print("\n(mlflow non installé : registry ignoré — pip install mlflow)")

print(f"\nArtefacts : {args.out}/")
