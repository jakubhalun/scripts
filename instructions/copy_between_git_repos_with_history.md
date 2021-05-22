## Copy code between git repositories with preserved history
<span style="color:gray; font-size: .8rem">_Note: It's best to follow this instruction on two freshly cloned repositories. Do not attempt to execute this on your working copy._</span>

### Source project
Clone project:
```
git clone source_project
cd source_project
```
This is just in case, to not accidentally push to old origin:
```
git remote rm origin
```
(Optional) If only a particular directory is needed:
```
git filter-branch --subdirectory-filter path/to/directory/source/directory
```
When this operation is finished, all the files from the selected subdirectory are moved to the repository's main directory.\
The next step is valid also for the case when you need to move all files from the source repository to a subdirectory of another repository:
```
mkdir new_directory_name

shopt -s extglob dotglob
mv !(new_directory_name|.git) new_directory_name
shopt -u dotglob
```
<span style="color:gray; font-size: .8rem">_Note: extglob, if set, turns  ON the extended pattern matching features._</span>\
<span style="color:gray; font-size: .8rem">_Note: dotglob, if set, turns  ON matching for hidden files._</span>

Commit changes:
```
git add .
git commit -m "files moved to the new directory"
cd ..
```

### Target project
Clone project:
```
git clone target_project
cd target_project
```
Add source project as remote:
```
git remote add source_project ../source_project
```
Merge master from source project to target project:
```
git fetch source_project
git branch source_project remotes/source_project/master
git merge source_project --allow-unrelated-histories
```
<span style="color:gray; font-size: .8rem">_Note: allow-unrelated-histories is required for this operation since git 2.9._</span>

Remove source project from remote:
```
git remote rm source_project
```
Push changes:
```
git push
```
