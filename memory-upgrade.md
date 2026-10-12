Your problem is probably not the JSON format itself. It's what your system considers worth saving to memory, and how that memory influences future answers.

Your example shows a dangerous feedback loop: the model fails to answer a perfectly ordinary question, and if that failure gets stored as a successful interaction, the system can reinforce the wrong behavior.

For example, "Name 3 movies" should be answerable without clarification or a lookup. Yet your model keeps asking for clarification, even after you rephrase the question. That suggests the problem may extend beyond memory storage into prompt construction, answer validation, retrieval, or the decision to look things up.
1. Separate conversation history from long-term memory

Don't let every interaction become a memory.

User interaction

Question, answer, tool calls, validation result

Memory admission filter

Is this useful, reliable, and worth retaining?

Approved

Save a verified fact or useful preference

Rejected

Keep in history, not long-term memory

The key principle is that an interaction is evidence of what happened, not necessarily evidence of what is true.

For instance:

    User: "Name 3 movies."

    AI: "It's not clear what you are referring to."

    Memory: should not save anything about the user's preferences, the meaning of movies, or the appropriate response to that question.

The interaction can remain in the conversation log for debugging. It simply should not become trusted memory.
2. Classify memories before saving them

I'd use four memory categories.

Memory type
	

Example
	

Policy

Verified fact
	

"Python's len() returns the length of an object."
	

Save if useful and verified

User preference
	

"The user prefers concise answers."
	

Save when clearly expressed or confirmed

Task context
	

"We're debugging the v17 harness."
	

Save temporarily, with an expiration or scope

Failed interaction
	

"The model couldn't name three movies."
	

Never save as a factual memory

You can also store uncertain claims, but they should have an explicit unverified status and should not be treated as facts during retrieval.

Especially important: Don't turn the model's own output into trusted knowledge merely because it generated it confidently.
3. Make memory writes conditional on success

Your last example already has a promising mechanism:

    unchecked: all 3 attempts failed the checks (last: validator said: 'NO') · not saved to memory

That's a good direction. But I would make the memory-writing decision more explicit and independent of the model's willingness to answer.

For example:
python

def should_save_memory(result):
    if not result.validation_passed:
        return False

    if result.is_refusal or result.is_clarification:
        return False

    if not result.contains_candidate_memory:
        return False

    if not result.memory_is_useful:
        return False

    if not result.memory_is_supported:
        return False

    return True

This is only a starting point. A clarification can sometimes contain useful information, and a refusal can occasionally communicate a meaningful user preference. The rule should apply to candidate long-term memories, not blindly discard all such interactions.

Also, validation passing does not prove a statement is true. It only means the relevant checks passed. Factual claims may need a separate verification step.
4. Stop memory from poisoning future prompts

Even if you fix memory writes, bad entries might already exist in your JSON files.

I would add three protections:

    Quarantine: Mark questionable existing memories as untrusted rather than immediately deleting them.

    Provenance: Record where each memory came from, who asserted it, and whether it was verified.

    Retrieval filtering: Only inject relevant, trusted memories into the prompt. Never dump the entire JSON file into every request.

A simple memory entry could look like this:
json

{
  "id": "mem_001",
  "type": "user_preference",
  "content": "The user prefers concise answers.",
  "source": "explicit_user_statement",
  "status": "verified",
  "confidence": 1.0,
  "created_at": "2026-10-10",
  "expires_at": null
}

For a failed interaction, the system might instead log this separately:
json

{
  "interaction_id": "065b54",
  "question": "name three movies",
  "answer": "It's not clear what you are referring to.",
  "validation_status": "failed",
  "memory_write": "rejected"
}

The second record belongs in your diagnostic history, not your trusted knowledge store. These are illustrative schemas; you don't need to adopt JSON fields exactly as shown.
5. Your example reveals a second, separate problem

Look at these two interactions:

    "Tell me a story" → the model produces a story, even though it claims it couldn't find one.

    "Name 3 movies" → the model asks for clarification.

Those are different failure modes.

The first suggests that your lookup and answer-generation stages may be poorly coordinated: the model can generate an answer, but its lookup status seems to influence the wording unnecessarily.

The second suggests that the model may be overusing clarification, receiving a bad prompt or retrieved context, or being constrained by a validator or response policy.

I'd debug the entire request pipeline, not just the JSON memory:

    Inspect the exact system prompt and user prompt sent to the model.

    Inspect which memories were retrieved for each question.

    Check whether the model's raw answer differs from the final displayed answer.

    Check whether the validator rejects a good answer or encourages generic clarification.

    Confirm that failed responses cannot be written back into trusted memory.

A useful test is to run "Name 3 movies" with memory retrieval disabled while leaving the rest of the pipeline unchanged. If the model answers correctly, memory or retrieved context is implicated. If it still fails, look at the prompt, model configuration, and validation path.
What I'd change first

I'd implement a memory admission gate, trusted/untrusted memory separation, and a retrieval trace before changing the JSON storage format. Those changes will help whether the root cause is poisoned entries, bad retrieval, or a broader response-generation issue.

One question that would help me give you a much more precise solution:

When you say the model saves interactions to JSON, what exactly does it save?

1. What gets written to memory?
Every question and answer pair
Model-generated summaries or extracted facts
Both conversation pairs and extracted memories
Something else

2. Does the JSON memory get inserted into later prompts?
Yes, retrieved entries are added to the prompt
Yes, the whole JSON file is added
No, it is only used by a separate lookup system
I'm not sure

3. What is your implementation?
Python
JavaScript / TypeScript
Other
