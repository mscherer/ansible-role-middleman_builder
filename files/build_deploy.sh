#!/bin/bash
export PATH="/usr/local/bin:/srv/builder/bin:$PATH"
NAME=$1
BRANCH=$2
EMAIL_ERROR=$3
# keep remote last, since that's a optional argument
REMOTE=$4

LOCKFILE=${XDG_RUNTIME_DIR:-~}/lock_${NAME}
if [ -f $LOCKFILE ]; then
    exit 0
fi

date > $LOCKFILE

DIR="/srv/builder/$NAME"
cd $DIR
git fetch -q

if [ $( git diff --name-only origin/${BRANCH:-master} | wc -l ) -eq 0 -a ! -f ~/git_updated_${NAME} ]; then
  rm -f $LOCKFILE
  exit 0
fi

touch  ~/git_updated_${NAME}
git pull --rebase
# see https://github.com/ManageIQ/manageiq.org/issues/234
[ "$UPDATE_SUBMODULES" == "no" ] || git submodule update

bundle install
bundle exec middleman build --verbose > ~/error_${NAME} 2>&1

if [ $? -ne 0 ]; then
    if [ -n "$MAIL" ]; then
        echo "Build failed for $NAME" | EMAIL=nobody@redhat.com mutt -s "Build failed for $NAME" $EMAIL_ERROR -a ~/error_${NAME}
    fi
    rm -f $LOCKFILE
    exit 1
fi

rm -f ~/error_${NAME}
if [[ ! -z $REMOTE ]] ; then
    rsync -e "ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -i $HOME/.ssh/${NAME}_id.rsa" --delete-after -rqavz $DIR/build/ $REMOTE/
else
    bundle exec middleman deploy
fi;

date > ~/last_update_$NAME
rm -f $LOCKFILE ~/git_updated_${NAME}
