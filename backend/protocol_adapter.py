"""A2UI v0.9.1 custom catalog subset, server-built readonly candidate surface."""
CATALOG = "urn:travel-genui:readonly-catalog:1"

def surface(candidate, scope):
    ids = [aid for did in candidate["document"]["dayOrder"] for aid in candidate["document"]["entities"]["days"][did]["activityIds"] if aid in scope["eligibleActivityIds"]]
    sid=candidate["candidateId"]
    return [{"version":"v0.9.1","createSurface":{"surfaceId":sid,"catalogId":CATALOG}},
            {"version":"v0.9.1","updateComponents":{"surfaceId":sid,"components":[{"id":"root","component":"TripCard","children":["activity-"+str(i) for i in range(len(ids))]}]+[{"id":"activity-"+str(i),"component":"ActivityCard","activityId":aid} for i,aid in enumerate(ids)]}}]
