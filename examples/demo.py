from learning_support_collab.main import run_demo, run_lifecycle_demo


for result in run_demo():
    print(f"{result['request_id']}: {result['decision']} -> {result['next_owner']}")

print(run_lifecycle_demo())
