#!/bin/bash
# Deploy Pepper Bridge Service to Pepper robot

PEPPER_IP="10.0.100.100"
PEPPER_USER="nao"
PEPPER_PASS="nao"
BRIDGE_FILE="pepper_bridge.py"
REMOTE_PATH="/home/nao/pepper_bridge.py"

echo "Deploying Pepper Bridge Service..."
echo ""

# Check if file exists
if [ ! -f "$BRIDGE_FILE" ]; then
    echo "ERROR: $BRIDGE_FILE not found!"
    exit 1
fi

# Deploy via SSH with expect
expect << EOF
spawn scp -o StrictHostKeyChecking=no $BRIDGE_FILE $PEPPER_USER@$PEPPER_IP:$REMOTE_PATH
expect {
    "password:" {
        send "$PEPPER_PASS\r"
        exp_continue
    }
    "Password:" {
        send "$PEPPER_PASS\r"
        exp_continue
    }
    eof {
    }
}
EOF

if [ $? -eq 0 ]; then
    echo "✓ File deployed successfully"
    
    # Make it executable
    expect << EOF
    spawn ssh -o StrictHostKeyChecking=no $PEPPER_USER@$PEPPER_IP "chmod +x $REMOTE_PATH"
    expect {
        "password:" {
            send "$PEPPER_PASS\r"
            exp_continue
        }
        "Password:" {
            send "$PEPPER_PASS\r"
            exp_continue
        }
        eof {
        }
    }
EOF
    
    echo "✓ Made executable"
    echo ""
    echo "To run the bridge service on Pepper:"
    echo "  ssh $PEPPER_USER@$PEPPER_IP"
    echo "  python $REMOTE_PATH"
    echo ""
    echo "Or run it in the background:"
    echo "  nohup python $REMOTE_PATH > /tmp/pepper_bridge.log 2>&1 &"
else
    echo "✗ Deployment failed"
    exit 1
fi

