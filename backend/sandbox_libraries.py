# sandbox_libraries.py
# Single source of truth for libraries available inside the sandboxed exec namespace.
# Consumed by: session.py (builds the namespace), llm.py (tells the LLM what's available)
# PRD: supports #3 (Q&A code execution) and #5 (guided ML)
#
# When adding or removing a library here, both the exec namespace and the LLM system
# prompt update automatically — no manual sync needed.

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import sklearn

# Maps the short name the LLM uses in generated code → the actual module object.
# The keys here are the exact variable names available inside exec().
SANDBOX_NAMESPACE_LIBRARIES: dict[str, object] = {
    "pd": pd,
    "np": np,
    "plt": plt,
    "sns": sns,
    "sklearn": sklearn,
}

# Human-readable descriptions for each library, used when building the LLM system prompt.
# Kept next to the imports so they stay in sync.
SANDBOX_LIBRARY_DESCRIPTIONS: dict[str, str] = {
    "pd": "pandas — dataframe manipulation (aliased as pd)",
    "np": "numpy — numerical operations (aliased as np)",
    "plt": "matplotlib.pyplot — plotting (aliased as plt)",
    "sns": "seaborn — statistical visualization (aliased as sns)",
    "sklearn": "scikit-learn — machine learning models and utilities",
}
