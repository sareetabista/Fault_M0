import pandas as pd

# 1. Load the data
df = pd.read_csv('clock_glitch_results_apprcycle_auto.csv')

# 2. Group by ext_offset and get the row count for each group
grouped_counts = df.groupby('ext_offset').size().reset_index(name='count')
# Group by ext_offset and count the frequency of each status
status_breakdown = df.groupby('ext_offset')['status'].value_counts().reset_index(name='count')
print(grouped_counts)
print(status_breakdown)
# 3. See the results
