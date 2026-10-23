from learning_support_collab.main import run_demo


for result in run_demo():
    print(f"{result['request_id']}: {result['decision']} -> {result['next_owner']}")
