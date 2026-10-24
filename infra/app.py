import os

import aws_cdk as cdk

from stack import ClausePathStack

app = cdk.App()
# The stack id keeps the working name "TripClaim": renaming it would replace every resource.
ClausePathStack(app, "TripClaim", env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
                                                      region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1")))
app.synth()
