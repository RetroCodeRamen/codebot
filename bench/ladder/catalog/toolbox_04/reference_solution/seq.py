import sys
from common import options, out

opts, args = options(sys.argv[1:], "w", "s")
nums = [int(a) for a in args]
first, step, last = (1, 1, nums[0]) if len(nums) == 1 else (nums[0], 1, nums[1]) if len(nums) == 2 else nums
vals, n = [], first
while (step > 0 and n <= last) or (step < 0 and n >= last): vals.append(n); n += step
width = max(len(str(first)), len(str(last))) if "w" in opts else 0
if vals: out(opts.get("s", "\n").join(str(v).zfill(width) for v in vals) + "\n")
