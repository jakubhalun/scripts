Print rollout history for all Kubernetes deployments:

```kubectl get deployments --kubeconfig ./kubeconfig | tail -n+2 | awk '{print $1;}' | xargs -n1 -I{} kubectl --kubeconfig ./kubeconfig rollout history deployment {}```
