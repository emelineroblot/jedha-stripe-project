// ============================================================
// Initialisation MongoDB — stripe_nosql
// Replica set, collections (time-series + validators), index, TTL
// Exécuté par : mongosh --file /scripts/mongo_init.js
// Miroir exécutable de schemas/nosql_schema.json
// ============================================================

// Replica set mono-nœud : nécessaire pour les transactions multi-documents et les change streams
try {
  rs.status();
} catch (e) {
  rs.initiate({ _id: "rs0", members: [{ _id: 0, host: "localhost:27017" }] });
  sleep(2000);
}

const dbn = db.getSiblingDB("stripe_nosql");
dbn.dropDatabase();

// ───────────── logs : time-series collection, TTL 90 jours ─────────────
dbn.createCollection("logs", {
  timeseries: { timeField: "timestamp", metaField: "meta", granularity: "seconds" },
  expireAfterSeconds: 90 * 24 * 3600,
});
dbn.logs.createIndex({ "meta.severity": 1, timestamp: -1 });
dbn.logs.createIndex({ "metadata.transaction_id": 1 });

// ───────────── user_sessions ─────────────
dbn.createCollection("user_sessions", {
  validator: {
    $jsonSchema: {
      required: ["customer_id", "session_start", "device", "events"],
      properties: {
        session_start: { bsonType: "date" },
        events: { bsonType: "array", maxItems: 500 },
        converted: { bsonType: "bool" },
      },
    },
  },
  validationLevel: "moderate",
});
dbn.user_sessions.createIndex({ customer_id: 1, session_start: -1 });
dbn.user_sessions.createIndex({ session_start: 1 }, { expireAfterSeconds: 180 * 24 * 3600 }); // TTL 180 j
dbn.user_sessions.createIndex({ transaction_id: 1 }, { sparse: true });
dbn.user_sessions.createIndex({ converted: 1, event_count: -1 });
dbn.user_sessions.createIndex({ "device.type": 1 });

// ───────────── ml_features ─────────────
dbn.createCollection("ml_features", {
  validator: {
    $jsonSchema: {
      required: ["transaction_id", "computed_at", "model_target", "features", "prediction"],
      properties: {
        computed_at: { bsonType: "date" },
        prediction: {
          bsonType: "object",
          required: ["fraud_probability", "risk_level", "model_version"],
          properties: {
            fraud_probability: { bsonType: "double", minimum: 0, maximum: 1 },
            risk_level: { enum: ["low", "medium", "high", "critical"] },
          },
        },
      },
    },
  },
});
dbn.ml_features.createIndex({ transaction_id: 1 }, { unique: true });
dbn.ml_features.createIndex({ customer_id: 1, computed_at: -1 });
dbn.ml_features.createIndex({ "prediction.risk_level": 1, computed_at: -1 });
dbn.ml_features.createIndex({ "prediction.model_version": 1 });
dbn.ml_features.createIndex({ "label.is_fraud": 1, computed_at: -1 });

// ───────────── customer_feedback ─────────────
dbn.createCollection("customer_feedback", {
  validator: {
    $jsonSchema: {
      required: ["customer_id", "merchant_id", "submitted_at", "scores"],
      properties: {
        submitted_at: { bsonType: "date" },
        scores: {
          bsonType: "object",
          required: ["overall"],
          properties: { overall: { bsonType: "int", minimum: 1, maximum: 5 } },
        },
        nps_score: { bsonType: "int", minimum: 0, maximum: 10 },
      },
    },
  },
});
dbn.customer_feedback.createIndex({ merchant_id: 1, submitted_at: -1 });
dbn.customer_feedback.createIndex({ country_code: 1, "scores.overall": 1 });
dbn.customer_feedback.createIndex({ "sentiment.label": 1 });
dbn.customer_feedback.createIndex({ tags: 1 });
dbn.customer_feedback.createIndex({ text: "text" });

// ───────────── recommendations ─────────────
dbn.createCollection("recommendations");
dbn.recommendations.createIndex({ merchant_id: 1, valid_until: -1 });
dbn.recommendations.createIndex({ model_version: 1 });

// ───────────── documents (métadonnées GridFS) ─────────────
dbn.createCollection("documents");
dbn.documents.createIndex({ dispute_id: 1 });
dbn.documents.createIndex({ merchant_id: 1, uploaded_at: -1 });

print("stripe_nosql initialisée : " + dbn.getCollectionNames().join(", "));
