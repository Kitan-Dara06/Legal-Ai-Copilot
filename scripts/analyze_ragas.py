import pandas as pd
df = pd.read_csv('ragas_baseline.csv')

print('=== RAGAS EVALUATION DETAILED RESULTS ===\n')

# 1. Overall Averages
print('--- OVERALL AVERAGES ---')
print(f"Contextual Precision: {df['llm_context_precision_with_reference'].mean():.2f}")
print("  (Did we retrieve the exact chunks containing the answer?)")

print(f"Contextual Recall:    {df['context_recall'].mean():.2f}")
print("  (Out of all the information needed to answer the question, how much did we retrieve?)")

print(f"Faithfulness:         {df['faithfulness'].mean():.2f}")
print("  (Is the generated answer factual based *only* on the retrieved context, with no hallucinations?)")

print(f"Answer Relevancy:     {df['answer_relevancy'].mean():.2f}")
print("  (Does the answer directly and concisely address the user's question?)\n")

# 2. Detail by Question
print('--- SCORE BREAKDOWN BY QUESTION ---')
for i, row in df.iterrows():
    question = row['user_input'][:70] + "..." if len(row['user_input']) > 70 else row['user_input']
    p = row['llm_context_precision_with_reference']
    r = row['context_recall']
    f = row['faithfulness']
    a = row['answer_relevancy']
    
    print(f"Q{i+1}: {question}")
    print(f"  Precision: {p:.2f} | Recall: {r:.2f} | Faithfulness: {f:.2f} | Relevancy: {a:.2f}")
    if p < 0.5 or r < 0.5 or f < 0.5 or a < 0.5:
        print(f"  ⚠️  Potential issues with this query.")
    print()
